"""Factory for the text-to-speech client.

Adding a provider that is not OpenAI-compatible — Cartesia, ElevenLabs, a
vendor with its own request shape — is a new class implementing TTSClient plus
one branch here. Nothing in services/, voice/ or api/ changes, because nothing
there names a provider. That is the Open/Closed claim, and it is checkable:
grep the rest of the tree for "piper" and it appears only in settings defaults.
"""

from logging import getLogger

from src.app.core.settings.tts import TTSSettings
from src.app.interfaces.llm.tts_client import TTSClient

logger = getLogger(__name__)


def create_tts(settings: TTSSettings, language: str) -> TTSClient:
    voice = settings.resolve_voice(language)

    if settings.provider == "piper":
        from src.app.llm.piper_tts_client import PiperTTSClient

        logger.info("TTS: piper voice %s", voice)
        return PiperTTSClient(settings, voice)

    if settings.provider == "openai_compatible":
        from openai import AsyncOpenAI

        from src.app.llm.openai_compatible_tts_client import OpenAICompatibleTTSClient

        logger.info("TTS: OpenAI-compatible %s at %s", settings.model, settings.base_url)
        client = AsyncOpenAI(
            api_key=settings.api_key or "not-needed",
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
        )
        # A cloud voice name is not a piper voice name, so the default map is
        # only consulted for piper; anything else must be set explicitly.
        return OpenAICompatibleTTSClient(settings, settings.voice or "alloy", client)

    raise ValueError(f"Unsupported TTS provider: {settings.provider}")
