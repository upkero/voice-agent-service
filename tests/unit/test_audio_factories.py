"""The STT/TTS factories: provider selection, capabilities, and fallback wiring.

These do not make network calls — constructing a client is offline; only using
it needs a key. So the factory's job (pick the right implementation, declare the
right streaming capability, wrap a fallback) is fully testable here.
"""

from unittest.mock import Mock

import pytest
from livekit.agents import stt, tts
from livekit.agents import vad as vad_module

from src.app.core.settings.stt import STTSettings
from src.app.core.settings.tts import TTSSettings
from src.app.llm.stt_factory import create_stt
from src.app.llm.tts_factory import create_tts


# _env_file=None so these construct from the given kwargs alone, not the
# project .env — otherwise a key sitting in .env would mask a validation test.
def _stt(**kw: object) -> STTSettings:
    return STTSettings(_env_file=None, **kw)  # type: ignore[arg-type]


def _tts(**kw: object) -> TTSSettings:
    return TTSSettings(_env_file=None, **kw)  # type: ignore[arg-type]


# --- STT provider selection ---------------------------------------------------
def test_openai_compatible_is_batch() -> None:
    client = create_stt(
        _stt(provider="openai_compatible", base_url="https://x/api/v1", api_key="k", fallback_provider=None), "ru"
    )
    assert client.capabilities.streaming is False


def test_deepgram_is_streaming() -> None:
    client = create_stt(_stt(provider="deepgram", api_key="dg", model="nova-3", fallback_provider=None), "ru")
    assert client.capabilities.streaming is True


def test_whisper_stream_is_streaming() -> None:
    client = create_stt(
        _stt(provider="whisper_stream", whisper_stream_url="ws://whisper:9090", fallback_provider=None), "ru"
    )
    assert client.capabilities.streaming is True


def test_unknown_stt_provider_is_rejected_by_settings() -> None:
    with pytest.raises(ValueError):
        _stt(provider="carrier-pigeon")


# --- STT settings validation --------------------------------------------------
@pytest.mark.parametrize(
    ("kwargs", "missing"),
    [
        ({"provider": "deepgram"}, "STT_API_KEY"),
        ({"provider": "whisper_stream"}, "STT_WHISPER_STREAM_URL"),
        ({"provider": "openai_compatible"}, "STT_BASE_URL"),
    ],
)
def test_streaming_providers_demand_their_config(kwargs: dict[str, str], missing: str) -> None:
    with pytest.raises(ValueError, match=missing):
        _stt(**kwargs)


def test_a_fallback_equal_to_the_primary_is_dropped() -> None:
    """faster_whisper is both the default primary and the default fallback, so
    this is the out-of-the-box case rather than a mistake to reject."""
    assert _stt(provider="faster_whisper", fallback_provider="faster_whisper").fallback_provider is None


# --- Fallback wiring ----------------------------------------------------------
def test_no_fallback_returns_a_single_client() -> None:
    client = create_stt(_stt(provider="deepgram", api_key="dg", model="nova-3", fallback_provider=None), "ru")
    assert not isinstance(client, stt.FallbackAdapter)


def test_a_cloud_primary_gets_the_offline_fallback_without_being_asked() -> None:
    """The point of the default: nobody has to know the setting exists for a
    Deepgram outage to stop being the guest's problem."""
    client = create_stt(
        _stt(provider="deepgram", api_key="dg", model="nova-3"),
        "ru",
        vad=Mock(spec=vad_module.VAD),
    )
    assert isinstance(client, stt.FallbackAdapter)


def test_the_default_tts_fallback_is_the_offline_voice() -> None:
    client = create_tts(_tts(provider="cartesia", api_key="ct", voice="v"), "ru")
    assert isinstance(client, tts.FallbackAdapter)


def test_two_streaming_providers_wrap_in_a_fallback_adapter() -> None:
    client = create_stt(
        _stt(
            provider="deepgram",
            api_key="dg",
            model="nova-3",
            fallback_provider="whisper_stream",
            whisper_stream_url="ws://whisper:9090",
        ),
        "ru",
    )
    assert isinstance(client, stt.FallbackAdapter)


def test_a_batch_fallback_without_a_vad_fails_loudly() -> None:
    """The FallbackAdapter needs a VAD to segment a batch member; in production
    build_session supplies Silero. Missing it is a startup error, not silence."""
    with pytest.raises(ValueError, match="streaming"):
        create_stt(
            _stt(
                provider="deepgram",
                api_key="dg",
                model="nova-3",
                fallback_provider="openai_compatible",
                base_url="https://x/api/v1",
            ),
            "ru",
            vad=None,
        )


# --- TTS ----------------------------------------------------------------------
def test_cartesia_builds() -> None:
    client = create_tts(_tts(provider="cartesia", api_key="ct", voice="v", fallback_provider=None), "ru")
    assert isinstance(client, tts.TTS)


def test_cartesia_demands_a_key() -> None:
    with pytest.raises(ValueError, match="TTS_API_KEY"):
        _tts(provider="cartesia")


def test_tts_fallback_wraps() -> None:
    client = create_tts(_tts(provider="cartesia", api_key="ct", fallback_provider="piper"), "ru")
    assert isinstance(client, tts.FallbackAdapter)


def test_piper_needs_no_key() -> None:
    client = create_tts(_tts(provider="piper"), "ru")  # its own fallback is dropped
    assert isinstance(client, tts.TTS)


def test_a_cloud_voice_name_is_not_used_for_the_piper_fallback() -> None:
    # TTS_VOICE="Kore" is the primary's (Gemini) voice. The piper fallback behind it must
    # pick its own per language, or it would look for a voice file that was never installed.
    settings = _tts(provider="cartesia", api_key="ct", voice="Kore")

    assert settings.resolve_voice("en") == "Kore"
    assert settings.resolve_voice("en", as_fallback=True) == "en_US-amy-medium"
    assert settings.resolve_voice("ru", as_fallback=True) == "ru_RU-irina-medium"


def test_an_explicit_voice_still_wins_when_piper_is_the_primary() -> None:
    assert _tts(provider="piper", voice="ru_RU-irina-medium").resolve_voice("en") == "ru_RU-irina-medium"
