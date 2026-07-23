from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

STTProvider = Literal["faster_whisper", "openai_compatible"]


class STTSettings(BaseSettings):
    """Speech-to-text configuration, independent of TTS.

    These are two separate ports with separate failure modes, so they get two
    separate settings classes. Running local Whisper against a cloud TTS — or
    the reverse — is a supported combination, not an accident.
    """

    provider: STTProvider = Field(
        default="faster_whisper",
        description="faster_whisper runs locally with no API key; openai_compatible calls any OpenAI-shaped audio API.",
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
        description="API key for the OpenAI-compatible provider.",
    )
    compute_type: str = Field(
        default="int8",
        description="faster_whisper compute type. int8 keeps CPU transcription real-time on a laptop.",
    )
    timeout_seconds: float = Field(default=30.0, gt=0, description="Request timeout for the cloud provider.")
    max_retries: int = Field(default=2, ge=0, description="SDK-level retries for the cloud provider.")

    model_config = SettingsConfigDict(env_prefix="STT_", env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_provider_requirements(self) -> "STTSettings":
        # Fail at construction rather than on the first utterance: a missing
        # base URL is a config mistake, and discovering it mid-call means a
        # guest hears silence while the logs explain why.
        if self.provider == "openai_compatible" and not self.base_url:
            raise ValueError("STT_BASE_URL is required when STT_PROVIDER='openai_compatible'.")
        return self


@lru_cache(maxsize=1)
def get_stt_settings() -> STTSettings:
    return STTSettings()
