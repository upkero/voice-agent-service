"""Factory for the text-to-speech client.

Three providers:

    piper              local binary     (own TTSClient, offline)
    openai_compatible  batch REST       (own TTSClient, OpenRouter/OpenAI)
    cartesia           streaming WS     (the livekit plugin, used directly)

Adding a provider is a branch here plus, only if it is not already an
stt/tts.TTS, a class implementing the TTSClient port. Cartesia's plugin already
is a tts.TTS, so it is constructed directly. Nothing in services/, voice/ or
api/ names a provider — grep the tree for "cartesia" and it appears only here
and in settings. That is the Open/Closed claim, and it is checkable.
"""

from logging import getLogger
from typing import Any

from livekit.agents import tts

from src.app.core.settings.tts import TTSProvider, TTSSettings
from src.app.exceptions.llm import LLMConfigurationError

logger = getLogger(__name__)


def create_tts(settings: TTSSettings, language: str) -> tts.TTS[Any]:
    """Build the TTS the session will use, with an optional fallback.

    A streaming primary (cartesia) paired with a local piper fallback means a
    provider outage degrades to an offline voice rather than to silence.
    """
    primary = _build_one(settings.provider, settings, language)
    if settings.fallback_provider is None:
        return primary

    fallback = _build_one(settings.fallback_provider, settings, language, as_fallback=True)
    logger.info("TTS fallback enabled: %s -> %s", settings.provider, settings.fallback_provider)
    return tts.FallbackAdapter([primary, fallback])


def create_primary_tts(settings: TTSSettings, language: str) -> tts.TTS[Any]:
    """The primary provider alone, with no fallback wrapper.

    For work that wants exactly one voice and a client it can close: a FallbackAdapter
    does not close its members.
    """
    return _build_one(settings.provider, settings, language)


def _build_one(
    provider: TTSProvider, settings: TTSSettings, language: str, *, as_fallback: bool = False
) -> tts.TTS[Any]:
    if provider == "piper":
        from src.app.llm.piper_tts_client import PiperTTSClient

        voice = settings.resolve_voice(language, as_fallback=as_fallback)
        logger.info("TTS: piper voice %s", voice)
        return PiperTTSClient(settings, voice)

    if provider == "openai_compatible":
        from openai import AsyncOpenAI

        from src.app.llm.openai_compatible_tts_client import OpenAICompatibleTTSClient

        logger.info("TTS: OpenAI-compatible %s at %s", settings.model, settings.base_url)
        client = AsyncOpenAI(
            api_key=settings.api_key or "not-needed",
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
        )
        # A cloud voice name is not a piper voice name, so the default map is not
        # consulted here; the provider's own voice must be set explicitly.
        return OpenAICompatibleTTSClient(settings, settings.voice or "alloy", client)

    if provider == "cartesia":
        from livekit.plugins import cartesia

        # Streaming, ~90ms to first byte. sonic is Cartesia's low-latency model.
        model = settings.model if settings.model != "tts-1" else "sonic-2"
        # Guaranteed by TTSSettings validation; typed rather than asserted so it
        # survives python -O and arrives in the standard error envelope.
        if settings.api_key is None:
            raise LLMConfigurationError("TTS_API_KEY (a Cartesia key) is required when TTS uses the cartesia provider.")
        logger.info("TTS: Cartesia %s (streaming, %s)", model, language)
        # kwargs typed Any so the optional voice can be omitted (letting the
        # plugin's default stand) without tripping the typed constructor.
        kwargs: dict[str, Any] = {"model": model, "language": language, "api_key": settings.api_key}
        if settings.voice:
            kwargs["voice"] = settings.voice
        return cartesia.TTS(**kwargs)

    raise ValueError(f"Unsupported TTS provider: {provider}")
