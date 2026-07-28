"""Adapter: the piper binary behind the project's TTS port.

The offline default voice. Piper is driven as a subprocess rather than through
the `piper-tts` wheel deliberately: the wheel drags in phonemiser and
onnxruntime builds whose availability varies by Python version and platform,
and a demo that fails to install is worse than one that shells out. The binary
is a single static download, pinned in the Dockerfile, and its stdin/stdout
contract has been stable for years.
"""

import asyncio
from logging import getLogger
from pathlib import Path
from typing import cast

from livekit.agents import APIConnectionError, tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

from src.app.core.settings.tts import TTSSettings
from src.app.interfaces.llm.tts_client import TTSClient

logger = getLogger(__name__)

_NUM_CHANNELS = 1


class PiperTTSClient(TTSClient):
    def __init__(self, settings: TTSSettings, voice: str) -> None:
        # streaming=False: piper synthesises a whole utterance per invocation.
        # The session splits long replies into sentences on its own, which is
        # why the speech policy in the prompt asks for short ones.
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=settings.sample_rate,
            num_channels=_NUM_CHANNELS,
        )
        self._settings = settings
        self._voice = voice
        self._model_path = Path(settings.voices_dir) / f"{voice}.onnx"

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        return _PiperStream(tts=self, input_text=text, conn_options=conn_options)

    @property
    def model_path(self) -> Path:
        return self._model_path

    @property
    def settings(self) -> TTSSettings:
        return self._settings


class _PiperStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        # livekit types `self._tts` as the base tts.TTS; this stream is only ever
        # constructed by PiperTTSClient, so the narrowing is a fact about
        # the framework rather than a check. A raised exception here would be a
        # branch that cannot run — and if it somehow did, it would be silence in
        # the middle of a phone call.
        client = cast(PiperTTSClient, self._tts)
        settings = client.settings

        if not client.model_path.exists():
            raise APIConnectionError(f"Piper voice not found: {client.model_path}")

        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=settings.sample_rate,
            num_channels=_NUM_CHANNELS,
            # Raw PCM rather than WAV: --output_raw skips the header, and a
            # header in the middle of a stream is just noise to decode around.
            mime_type="audio/pcm",
        )

        try:
            process = await asyncio.create_subprocess_exec(
                settings.binary_path,
                "--model",
                str(client.model_path),
                "--output_raw",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise APIConnectionError(f"Could not start piper ({settings.binary_path}): {exc}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(self._input_text.encode("utf-8")),
                timeout=settings.timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            raise APIConnectionError("Piper timed out while synthesising.") from exc

        if process.returncode != 0:
            raise APIConnectionError(f"Piper exited with {process.returncode}: {stderr.decode(errors='replace')[:200]}")
        if not stdout:
            raise APIConnectionError("Piper produced no audio.")

        output_emitter.push(stdout)
        output_emitter.flush()
