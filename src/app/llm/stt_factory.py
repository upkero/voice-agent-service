"""Factory for the speech-to-text client.

Independent of the TTS factory by design: STT and TTS are separate ports with
separate settings and separate failure modes, so running local Whisper against
a cloud voice — or the reverse — is a supported combination rather than an
accident of a shared "audio provider" switch.

Four providers, two of them true streaming:

    faster_whisper     local batch      (own STTClient, offline)
    openai_compatible  batch REST       (own STTClient, OpenRouter/OpenAI)
    deepgram           streaming WS     (the livekit plugin, used directly)
    whisper_stream     streaming WS     (own STTClient -> self-hosted WhisperLive)

Whether a provider streams is not decided here and not branched on anywhere in
the app: each client declares STTCapabilities(streaming=...), and the session
drives it accordingly — wrapping a batch one in its own VAD segmenter. This
factory only chooses *which* client, and optionally pairs two behind a
FallbackAdapter so a streaming primary degrades to a batch fallback at runtime.

Concrete clients are imported inside the branch, not at module scope, so the
HTTP process never imports ctranslate2 and a machine without one provider's
dependency can still use another.
"""

from collections.abc import Sequence
from logging import getLogger
from typing import Any

from livekit.agents import stt
from livekit.agents import vad as vad_module

from src.app.core.settings.stt import STTProvider, STTSettings

logger = getLogger(__name__)


def create_stt(settings: STTSettings, language: str, vad: vad_module.VAD | None = None) -> stt.STT[Any]:
    """Build the STT the session will use.

    With no fallback configured this returns one client. With a fallback it
    returns a FallbackAdapter over both, which fails the primary over to the
    secondary per request. The VAD is handed to the adapter so it can segment
    any batch member into the streaming interface the session expects.
    """
    primary = _build_one(settings.provider, settings, language)
    if settings.fallback_provider is None:
        return primary

    fallback = _build_one(settings.fallback_provider, settings, language)
    logger.info("STT fallback enabled: %s -> %s", settings.provider, settings.fallback_provider)
    return stt.FallbackAdapter(_dedupe([primary, fallback]), vad=vad)


def _build_one(provider: STTProvider, settings: STTSettings, language: str) -> stt.STT[Any]:
    if provider == "faster_whisper":
        from src.app.llm.faster_whisper_stt_client import FasterWhisperSTTClient

        logger.info("STT: faster-whisper %s (%s)", settings.model, settings.language or language)
        return FasterWhisperSTTClient(settings, language)

    if provider == "openai_compatible":
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

    if provider == "deepgram":
        # The plugin already is an stt.STT with streaming=True, so it is used
        # directly — a wrapper would add a layer that satisfies an interface the
        # plugin already satisfies.
        from livekit.plugins import deepgram

        # "small" is the faster_whisper default and means nothing to Deepgram, so
        # a user who switched provider but not model still gets a valid model.
        model = settings.model if settings.model != "small" else "nova-3"
        # Guaranteed by STTSettings validation; the assert satisfies the type
        # checker and documents the invariant at the call site.
        assert settings.api_key is not None
        logger.info("STT: Deepgram %s (streaming)", model)
        return deepgram.STT(
            model=model,
            language=settings.language or language,
            api_key=settings.api_key,
            interim_results=True,
        )

    if provider == "whisper_stream":
        from src.app.llm.whisper_stream_stt_client import WhisperStreamSTTClient

        logger.info("STT: self-hosted WhisperLive at %s (streaming)", settings.whisper_stream_url)
        return WhisperStreamSTTClient(settings, language)

    raise ValueError(f"Unsupported STT provider: {provider}")


def _dedupe(clients: Sequence[stt.STT[Any]]) -> list[stt.STT[Any]]:
    """Guard against a fallback that is really the same object as the primary."""
    seen: list[stt.STT[Any]] = []
    for client in clients:
        if all(client is not existing for existing in seen):
            seen.append(client)
    return seen
