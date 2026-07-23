"""Adapter: any OpenAI-compatible speech API behind the project's TTS port.

The mirror image of the STT client, and generic for the same reason: no
provider is named here, the endpoint is TTS_BASE_URL, and switching from
OpenRouter to OpenAI to a local server is a value in .env.

Note what this is *not*. A provider with its own request shape — Cartesia, say —
is not "OpenAI-compatible" merely because it also returns audio. It belongs in
its own class implementing the same TTSClient port, added alongside this one
and selected by a new branch in the factory. That is the Open/Closed part: a
third provider changes two files that exist for choosing providers, and no
other line of this service.
"""

from logging import getLogger
from typing import Literal

from livekit.agents import APIConnectionError, tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from openai import AsyncOpenAI, OpenAIError

from src.app.core.settings.tts import TTSSettings
from src.app.interfaces.llm.tts_client import TTSClient

logger = getLogger(__name__)

_NUM_CHANNELS = 1
# PCM rather than mp3: the pipeline wants raw samples, and asking for a
# compressed container only to decode it again adds latency to every reply.
_RESPONSE_FORMAT: Literal["pcm"] = "pcm"
# What OpenAI-compatible speech endpoints emit for the pcm format.
_PCM_SAMPLE_RATE = 24000


class OpenAICompatibleTTSClient(TTSClient):
    def __init__(self, settings: TTSSettings, voice: str, client: AsyncOpenAI) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=_PCM_SAMPLE_RATE,
            num_channels=_NUM_CHANNELS,
        )
        self._settings = settings
        self._voice = voice
        self._client = client

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        return _OpenAICompatibleStream(tts=self, input_text=text, conn_options=conn_options)

    @property
    def voice(self) -> str:
        return self._voice

    @property
    def settings(self) -> TTSSettings:
        return self._settings

    @property
    def client(self) -> AsyncOpenAI:
        return self._client

    async def aclose(self) -> None:
        await self._client.close()
        await super().aclose()


class _OpenAICompatibleStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        client = self._tts
        assert isinstance(client, OpenAICompatibleTTSClient)

        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=_PCM_SAMPLE_RATE,
            num_channels=_NUM_CHANNELS,
            mime_type="audio/pcm",
        )

        try:
            async with client.client.audio.speech.with_streaming_response.create(
                model=client.settings.model,
                voice=client.voice,
                input=self._input_text,
                response_format=_RESPONSE_FORMAT,
            ) as response:
                # Streamed rather than buffered: the first chunk can start
                # playing while the rest is still arriving, which is the
                # difference between a reply that feels immediate and one that
                # lands a second late on every turn.
                async for chunk in response.iter_bytes():
                    output_emitter.push(chunk)
        except OpenAIError as exc:
            raise APIConnectionError(f"TTS provider request failed: {exc}") from exc

        output_emitter.flush()
