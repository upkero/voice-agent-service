"""Adapter: any OpenAI-compatible transcription API behind the project's STT port.

Generic on purpose. No provider is named in this file — the endpoint comes from
STT_BASE_URL, so pointing it at OpenRouter, OpenAI itself, a self-hosted
whisper.cpp server or anything else that implements
`POST /audio/transcriptions` is configuration, not a code change.

Built on the `openai` SDK rather than hand-rolled httpx: the multipart encoding,
authentication and retry policy are already there and correct, and re-writing
them would be new code whose only distinction is being less tested.
"""

import io
import wave
from logging import getLogger

from livekit.agents import APIConnectionError, NotGivenOr, stt, utils
from livekit.agents.language import LanguageCode
from livekit.agents.types import NOT_GIVEN, APIConnectOptions
from openai import AsyncOpenAI, OpenAIError

from src.app.core.settings.stt import STTSettings
from src.app.interfaces.llm.stt_client import STTClient

logger = getLogger(__name__)


class OpenAICompatibleSTTClient(STTClient):
    def __init__(self, settings: STTSettings, language: str, client: AsyncOpenAI) -> None:
        super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))
        self._settings = settings
        self._language = settings.language or language
        self._client = client

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        frame = utils.audio.combine_frames(buffer)
        target_language = language if isinstance(language, str) else self._language
        payload = _to_wav(frame.data.tobytes(), frame.sample_rate, frame.num_channels)

        try:
            transcription = await self._client.audio.transcriptions.create(
                # A filename is required by the multipart contract and the
                # extension is how most servers detect the container.
                file=("speech.wav", payload, "audio/wav"),
                model=self._settings.model,
                language=target_language,
            )
        except OpenAIError as exc:
            raise APIConnectionError(f"STT provider request failed: {exc}") from exc

        text = (transcription.text or "").strip()
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(language=LanguageCode(target_language), text=text)],
        )

    async def aclose(self) -> None:
        await self._client.close()
        await super().aclose()


def _to_wav(pcm: bytes, sample_rate: int, num_channels: int) -> bytes:
    """Wrap raw PCM in a WAV container.

    Raw samples carry no sample rate, so a server receiving them has to guess —
    and a wrong guess transcribes chipmunks. The header is 44 bytes and removes
    the whole class of problem.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(num_channels)
        handle.setsampwidth(2)  # LiveKit delivers 16-bit signed PCM
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()
