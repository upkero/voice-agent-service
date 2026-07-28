from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Ways to turn text into speech:
#   piper              - local binary, offline, no key (default)
#   openai_compatible  - batch REST (OpenRouter/OpenAI): first audio byte in ~2-3s
#   cartesia           - streaming over Cartesia's WebSocket, ~90ms to first byte (needs key)
# Cartesia is the low-latency path; the others buffer more before the first sound.
TTSProvider = Literal["piper", "openai_compatible", "cartesia"]

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
        description="Which TTS implementation to build. cartesia is streaming and low-latency.",
    )
    fallback_provider: TTSProvider | None = Field(
        default="piper",
        description=(
            "Second provider, wrapped with the primary in a FallbackAdapter. If the primary (e.g. "
            "streaming cartesia) fails, the session fails over to this one. Defaults to piper — the "
            "binary and both voices are baked into the agent image, so a cloud outage degrades to an "
            "offline voice instead of to silence. Ignored when it equals the primary; set to an empty "
            "value to run with no fallback at all."
        ),
    )
    voice: str | None = Field(
        default=None,
        description="Voice name for the chosen provider. Voice sets differ per provider/model.",
    )
    model: str = Field(
        default="tts-1",
        min_length=1,
        description="Model name: the openai_compatible speech model, or the Cartesia model id.",
    )
    base_url: str | None = Field(
        default=None,
        description="OpenAI-compatible speech API root, e.g. https://openrouter.ai/api/v1.",
    )
    api_key: str | None = Field(
        default=None,
        description="API key for the cloud provider: the OpenAI-compatible key, or the Cartesia key.",
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
        if self.fallback_provider == self.provider:
            # The default fallback is piper, which is also the default primary,
            # so this is the ordinary case rather than a mistake.
            self.fallback_provider = None

        providers = {self.provider, self.fallback_provider}
        if "openai_compatible" in providers and not self.base_url:
            raise ValueError("TTS_BASE_URL is required when TTS uses the openai_compatible provider.")
        if "cartesia" in providers and not self.api_key:
            raise ValueError("TTS_API_KEY (a Cartesia key) is required when TTS uses the cartesia provider.")
        return self

    def resolve_voice(self, language: str) -> str:
        """Explicit voice wins; otherwise the language decides.

        The default map holds piper voice names; a cloud provider needs its own
        voice set via TTS_VOICE, so this only meaningfully defaults for piper.
        """
        return self.voice or DEFAULT_PIPER_VOICES.get(language, DEFAULT_PIPER_VOICES["en"])


@lru_cache(maxsize=1)
def get_tts_settings() -> TTSSettings:
    return TTSSettings()
