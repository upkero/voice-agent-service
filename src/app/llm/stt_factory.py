"""Factory for the speech-to-text client.

Independent of the TTS factory by design: STT and TTS are separate ports with
separate settings and separate failure modes, so running local Whisper against
a cloud voice — or the reverse — is a supported combination rather than an
accident of a shared "audio provider" switch.

Concrete clients are imported inside the branch, not at module scope. That is
what keeps the HTTP process from ever importing ctranslate2, and what lets the
cloud provider work on a machine where the local one is not installed.
"""

from logging import getLogger

from src.app.core.settings.stt import STTSettings
from src.app.interfaces.llm.stt_client import STTClient

logger = getLogger(__name__)


def create_stt(settings: STTSettings, language: str) -> STTClient:
    if settings.provider == "faster_whisper":
        from src.app.llm.faster_whisper_stt_client import FasterWhisperSTTClient

        logger.info("STT: faster-whisper %s (%s)", settings.model, settings.language or language)
        return FasterWhisperSTTClient(settings, language)

    if settings.provider == "openai_compatible":
        from openai import AsyncOpenAI

        from src.app.llm.openai_compatible_stt_client import OpenAICompatibleSTTClient

        logger.info("STT: OpenAI-compatible %s at %s", settings.model, settings.base_url)
        client = AsyncOpenAI(
            api_key=settings.api_key or "not-needed",
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
        )
        return OpenAICompatibleSTTClient(settings, language, client)

    # Unreachable while STTProvider is a Literal, and kept anyway: the type
    # checker proves today's cases, not the case someone adds next year.
    raise ValueError(f"Unsupported STT provider: {settings.provider}")
