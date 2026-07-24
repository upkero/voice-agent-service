from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Four ways to turn speech into text, in rising order of latency effort:
#   faster_whisper     - local batch, CPU, offline, no key (default)
#   openai_compatible  - batch REST (OpenRouter/OpenAI), no streaming
#   deepgram           - streaming over Deepgram's native WebSocket (needs key)
#   whisper_stream     - streaming against a self-hosted WhisperLive server (no key)
# Only the last two are true streaming; the first two are batch and get wrapped
# by the session's VAD-driven StreamAdapter. See docs/architecture.md.
STTProvider = Literal["faster_whisper", "openai_compatible", "deepgram", "whisper_stream"]


class STTSettings(BaseSettings):
    """Speech-to-text configuration, independent of TTS.

    These are two separate ports with separate failure modes, so they get two
    separate settings classes. Running local Whisper against a cloud TTS — or
    the reverse — is a supported combination, not an accident.
    """

    provider: STTProvider = Field(
        default="faster_whisper",
        description="Which STT implementation to build. deepgram and whisper_stream are true streaming.",
    )
    fallback_provider: STTProvider | None = Field(
        default=None,
        description=(
            "Optional second provider. When set, the two are wrapped in a FallbackAdapter: if the "
            "primary (e.g. streaming deepgram) fails at connect time, the session fails over to this "
            "one per request. A natural pairing is a streaming primary with a batch fallback."
        ),
    )
    model: str = Field(
        default="small",
        min_length=1,
        description=(
            "faster_whisper: a Whisper size (tiny/base/small/medium) or a CTranslate2 model path. "
            "openai_compatible: the provider's transcription model name."
        ),
    )
    language: str | None = Field(
        default=None,
        description="Language hint (ISO-639-1). Unset means follow AGENT_LANGUAGE.",
    )
    base_url: str | None = Field(
        default=None,
        description=(
            "OpenAI-compatible audio API root, e.g. https://openrouter.ai/api/v1. "
            "Required when provider='openai_compatible'."
        ),
    )
    api_key: str | None = Field(
        default=None,
        description="API key for the cloud provider: the OpenAI-compatible key, or the Deepgram key.",
    )
    compute_type: str = Field(
        default="int8",
        description="faster_whisper compute type. int8 keeps CPU transcription real-time on a laptop.",
    )
    whisper_stream_url: str | None = Field(
        default=None,
        description=(
            "WebSocket URL of a self-hosted WhisperLive server, e.g. ws://whisper:9090. Required when "
            "provider (or fallback) is whisper_stream. In docker-compose it is the service name; in "
            "production it points at a separate GPU instance."
        ),
    )
    timeout_seconds: float = Field(default=30.0, gt=0, description="Request timeout for the cloud provider.")
    max_retries: int = Field(default=2, ge=0, description="SDK-level retries for the cloud provider.")

    model_config = SettingsConfigDict(env_prefix="STT_", env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_provider_requirements(self) -> "STTSettings":
        # Fail at construction rather than on the first utterance: a missing
        # base URL is a config mistake, and discovering it mid-call means a
        # guest hears silence while the logs explain why. Both the primary and
        # the fallback have to be usable, or the fallback is a false comfort.
        providers = {self.provider, self.fallback_provider}
        if "openai_compatible" in providers and not self.base_url:
            raise ValueError("STT_BASE_URL is required when STT uses the openai_compatible provider.")
        if "deepgram" in providers and not self.api_key:
            raise ValueError("STT_API_KEY (a Deepgram key) is required when STT uses the deepgram provider.")
        if "whisper_stream" in providers and not self.whisper_stream_url:
            raise ValueError("STT_WHISPER_STREAM_URL is required when STT uses the whisper_stream provider.")
        if self.fallback_provider is not None and self.fallback_provider == self.provider:
            raise ValueError("STT_FALLBACK_PROVIDER must differ from STT_PROVIDER.")
        return self


@lru_cache(maxsize=1)
def get_stt_settings() -> STTSettings:
    return STTSettings()
