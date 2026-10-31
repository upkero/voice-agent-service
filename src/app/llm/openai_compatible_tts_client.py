"""Adapter: any OpenAI-compatible speech API behind livekit's tts.TTS.

The mirror image of the STT client, and generic for the same reason: no
provider is named here, the endpoint is TTS_BASE_URL, and switching from
OpenRouter to OpenAI to a local server is a value in .env.

A provider with its own request shape is not "OpenAI-compatible" merely
because it also returns audio. It gets its own branch in the factory, and its
own tts.TTS subclass unless livekit already ships one: Cartesia's plugin is
used directly.
"""

from typing import Any, Literal, cast

from livekit.agents import APIConnectionError, tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from openai import AsyncOpenAI, OpenAIError

_NUM_CHANNELS = 1
# PCM rather than mp3: the pipeline wants raw samples, and asking for a
# compressed container only to decode it again adds latency to every reply.
_RESPONSE_FORMAT: Literal["pcm"] = "pcm"
# What OpenAI-compatible speech endpoints emit for the pcm format.
_PCM_SAMPLE_RATE = 24000


class OpenAICompatibleTTSClient(tts.TTS[Any]):
    def __init__(self, model: str, voice: str, client: AsyncOpenAI) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=_PCM_SAMPLE_RATE,
            num_channels=_NUM_CHANNELS,
        )
        self._model = model
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
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return self._client.base_url.host

    @property
    def voice(self) -> str:
        return self._voice

    @property
    def client(self) -> AsyncOpenAI:
        return self._client

    async def aclose(self) -> None:
        await self._client.close()
        await super().aclose()


class _OpenAICompatibleStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        # livekit types `self._tts` as the base tts.TTS; this stream is only ever
        # constructed by OpenAICompatibleTTSClient, so the narrowing is a fact about
        # the framework rather than a check. A raised exception here would be a
        # branch that cannot run — and if it somehow did, it would be silence in
        # the middle of a phone call.
        client = cast(OpenAICompatibleTTSClient, self._tts)

        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=_PCM_SAMPLE_RATE,
            num_channels=_NUM_CHANNELS,
            mime_type="audio/pcm",
        )

        try:
            async with client.client.audio.speech.with_streaming_response.create(
                model=client.model,
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
