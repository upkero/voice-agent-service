"""Adapter: a local Whisper model behind the project's STT port.

This is the offline default, and the reason the demo needs no account
anywhere. It also makes the Factory above it honest — two real products, not
one product and a placeholder — which is the same shape ops-core-api uses for
hashing embeddings.
"""

import asyncio
from functools import lru_cache
from logging import getLogger
from typing import Any

from livekit.agents import APIConnectionError, NotGivenOr, stt, utils
from livekit.agents.language import LanguageCode
from livekit.agents.types import NOT_GIVEN, APIConnectOptions

from src.app.core.settings.stt import STTSettings
from src.app.interfaces.llm.stt_client import STTClient

logger = getLogger(__name__)


@lru_cache(maxsize=1)
def load_model(name: str, compute_type: str) -> Any:
    """One Whisper model per process. The worker calls this from prewarm so the
    first call does not spend ~10 s loading it; every client then gets the cached one."""
    # Imported here, not at module scope: ctranslate2 is a heavy import and the HTTP
    # process must never pay for it.
    from faster_whisper import WhisperModel

    logger.info("Loading faster-whisper model %s (%s)", name, compute_type)
    return WhisperModel(name, device="cpu", compute_type=compute_type)


class FasterWhisperSTTClient(STTClient):
    def __init__(self, settings: STTSettings, language: str) -> None:
        # streaming=False: Whisper transcribes a finished utterance, it does not
        # emit partials. Declaring that truthfully matters — the session wraps a
        # non-streaming STT in its own VAD-driven segmenter, and claiming
        # otherwise would have it wait forever for interim results.
        super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))
        self._settings = settings
        self._language = settings.language or language
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()

    async def _ensure_model(self) -> Any:
        # Imported here, not at module scope: ctranslate2 is a heavy import and
        # the HTTP process must never pay for it. The lock stops two concurrent
        # first utterances from loading the model twice.
        async with self._load_lock:
            if self._model is None:
                self._model = await asyncio.to_thread(
                    load_model, self._settings.model, self._settings.compute_type
                )
            return self._model

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        model = await self._ensure_model()
        frame = utils.audio.combine_frames(buffer)
        target_language = language if isinstance(language, str) else self._language

        try:
            # to_thread because transcription is CPU-bound: running it inline
            # would block the event loop that is also moving audio frames, and
            # the guest would hear the agent stutter.
            text = await asyncio.to_thread(self._transcribe, model, frame.data.tobytes(), target_language)
        except Exception as exc:
            # Wrapped in livekit's error type so the session treats it as a
            # provider failure and the degradation path can announce it,
            # instead of it surfacing as an unexplained crash.
            raise APIConnectionError(f"faster-whisper transcription failed: {exc}") from exc

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(language=LanguageCode(target_language), text=text)],
        )

    def _transcribe(self, model: Any, pcm: bytes, language: str) -> str:
        import numpy as np

        # LiveKit hands over 16-bit signed PCM; Whisper wants float32 in [-1, 1].
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = model.transcribe(
            samples,
            language=language,
            # A restaurant caller says two sentences, not a lecture. Beam search
            # would cost latency on a live call for accuracy nobody notices.
            beam_size=1,
            vad_filter=True,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
