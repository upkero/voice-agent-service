from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

TTSProvider = Literal["piper", "openai_compatible"]

# Per-language default voices, used when TTS_VOICE is unset. Both are baked
# into the agent image at build time, so switching AGENT_LANGUAGE needs no
# download and no other change.
DEFAULT_PIPER_VOICES: dict[str, str] = {
    "ru": "ru_RU-irina-medium",
    "en": "en_US-amy-medium",
}


class TTSSettings(BaseSettings):
    """Text-to-speech configuration, independent of STT."""

    provider: TTSProvider = Field(
        default="piper",
        description="piper runs locally with no API key; openai_compatible calls any OpenAI-shaped speech API.",
    )
    voice: str | None = Field(
        default=None,
        description="piper: voice name, e.g. ru_RU-irina-medium. Unset picks the default for AGENT_LANGUAGE.",
    )
    model: str = Field(
        default="tts-1",
        min_length=1,
        description="openai_compatible: the provider's speech model name.",
    )
    base_url: str | None = Field(
        default=None,
        description="OpenAI-compatible speech API root, e.g. https://openrouter.ai/api/v1.",
    )
    api_key: str | None = Field(
        default=None,
        description="API key for the OpenAI-compatible provider.",
    )
    binary_path: str = Field(
        default="piper",
        description="piper executable. The agent image installs the prebuilt binary on PATH.",
    )
    voices_dir: str = Field(
        default="/opt/piper/voices",
        description="Directory holding <voice>.onnx and <voice>.onnx.json.",
    )
    sample_rate: int = Field(
        default=22050,
        gt=0,
        description="Sample rate piper emits. Medium-quality voices are 22.05 kHz.",
    )
    timeout_seconds: float = Field(default=30.0, gt=0, description="Request timeout for the cloud provider.")
    max_retries: int = Field(default=2, ge=0, description="SDK-level retries for the cloud provider.")

    model_config = SettingsConfigDict(env_prefix="TTS_", env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_provider_requirements(self) -> "TTSSettings":
        if self.provider == "openai_compatible" and not self.base_url:
            raise ValueError("TTS_BASE_URL is required when TTS_PROVIDER='openai_compatible'.")
        return self

    def resolve_voice(self, language: str) -> str:
        """Explicit voice wins; otherwise the language decides."""
        return self.voice or DEFAULT_PIPER_VOICES.get(language, DEFAULT_PIPER_VOICES["en"])


@lru_cache(maxsize=1)
def get_tts_settings() -> TTSSettings:
    return TTSSettings()
