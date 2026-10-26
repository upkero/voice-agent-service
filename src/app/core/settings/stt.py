from functools import lru_cache
from logging import getLogger
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

# The model each provider loads when STT_MODEL (primary) or STT_FALLBACK_MODEL
# (fallback) is unset. One table, so the primary and the fallback never share a
# model name that only one of them understands.
DEFAULT_STT_MODELS: dict[STTProvider, str] = {
    "faster_whisper": "small",
    "openai_compatible": "whisper-1",
    "deepgram": "nova-3",
    "whisper_stream": "small",
}

# Whisper model sizes, for recognising the legacy layout below.
_WHISPER_SIZES = frozenset(
    {"tiny", "base", "small", "medium", "large", "large-v1", "large-v2", "large-v3", "large-v3-turbo", "turbo"}
    | {f"{size}.en" for size in ("tiny", "base", "small", "medium")}
)

logger = getLogger(__name__)


class STTSettings(BaseSettings):
    """Speech-to-text configuration, independent of TTS.

    These are two separate ports with separate failure modes, so they get two
    separate settings classes. Any STT provider can run against any TTS
    provider; the pairing is configuration, not code.
    """

    provider: STTProvider = Field(
        default="faster_whisper",
        description="Which STT implementation to build. deepgram and whisper_stream are true streaming.",
    )
    fallback_provider: STTProvider | None = Field(
        default="faster_whisper",
        description=(
            "Second provider, wrapped with the primary in a FallbackAdapter: if the primary (a cloud "
            "provider) fails, the session fails over to this one per request. Defaults to "
            "faster_whisper — it is baked into the agent image, runs offline and costs nothing, so a "
            "cloud outage becomes something the guest never notices. Ignored when it equals the "
            "primary; set to an empty value to run with no fallback at all."
        ),
    )
    model: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Model of the primary provider. deepgram: a Deepgram model (nova-3). faster_whisper and "
            "whisper_stream: a Whisper size (tiny/base/small/medium) or a CTranslate2 model path. "
            "openai_compatible: the provider's transcription model name. Unset means the provider's "
            "default from DEFAULT_STT_MODELS."
        ),
    )
    fallback_model: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Model of the fallback provider, in that provider's own naming (a Whisper size for "
            "faster_whisper). Unset means the fallback provider's default from DEFAULT_STT_MODELS."
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
    deepgram_price_per_minute_usd: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Deepgram's price per audio minute, for voice_cost_usd_total. The one static price in the "
            "cost metrics: the LLM's cost is the exact figure OpenRouter reports for each request, the "
            "local providers cost nothing, and TTS spend is not recorded at all. 0 records no STT spend."
        ),
    )

    model_config = SettingsConfigDict(env_prefix="STT_", env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_provider_requirements(self) -> "STTSettings":
        # Fail at construction rather than on the first utterance: a missing
        # base URL is a config mistake, and discovering it mid-call means a
        # guest hears silence while the logs explain why. Both the primary and
        # the fallback have to be usable, or the fallback is a false comfort.
        if self.fallback_provider == self.provider:
            # The default fallback is faster_whisper, which is also the default
            # primary, so this is the ordinary case rather than a mistake. A
            # FallbackAdapter over one provider twice would only retry itself.
            self.fallback_provider = None
        self._migrate_shared_model()

        providers = {self.provider, self.fallback_provider}
        if "openai_compatible" in providers and not self.base_url:
            raise ValueError("STT_BASE_URL is required when STT uses the openai_compatible provider.")
        if "deepgram" in providers and not self.api_key:
            raise ValueError("STT_API_KEY (a Deepgram key) is required when STT uses the deepgram provider.")
        if "whisper_stream" in providers and not self.whisper_stream_url:
            raise ValueError("STT_WHISPER_STREAM_URL is required when STT uses the whisper_stream provider.")
        return self

    def _migrate_shared_model(self) -> None:
        """Read the layout from before STT_FALLBACK_MODEL existed.

        STT_MODEL used to serve both providers, so an .env pairing a Deepgram
        primary with a Whisper fallback set STT_MODEL to a Whisper size. Deepgram
        has no model of that name, so the value can only have been meant for the
        fallback: it moves there, and the primary takes its own default. Nothing
        else is reinterpreted; an explicit STT_FALLBACK_MODEL switches this off.
        """
        if (
            self.provider == "deepgram"
            and self.fallback_provider in ("faster_whisper", "whisper_stream")
            and self.fallback_model is None
            and self.model in _WHISPER_SIZES
        ):
            logger.warning(
                "STT_MODEL=%s is a Whisper size and the primary is deepgram: using it as "
                "STT_FALLBACK_MODEL and Deepgram's default as STT_MODEL. Set both explicitly.",
                self.model,
            )
            self.fallback_model, self.model = self.model, None

    def model_for(self, provider: STTProvider) -> str:
        """The model to load for the primary or the fallback provider."""
        if provider == self.provider:
            explicit = self.model
        elif provider == self.fallback_provider:
            explicit = self.fallback_model
        else:
            raise ValueError(f"STT provider {provider} is neither the primary nor the fallback.")
        return explicit or DEFAULT_STT_MODELS[provider]


@lru_cache(maxsize=1)
def get_stt_settings() -> STTSettings:
    return STTSettings()
