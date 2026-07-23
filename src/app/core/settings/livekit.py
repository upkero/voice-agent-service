from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LiveKitSettings(BaseSettings):
    """Connection details for the LiveKit server, shared by both processes.

    The HTTP process signs join tokens with the key pair; the agent worker uses
    the same pair to register itself. `livekit-server --dev` prints devkey/secret,
    which is why the defaults below are those and not a placeholder that fails.
    """

    url: str = Field(
        default="ws://localhost:7880",
        description=(
            "The URL that goes into a join token — the one the *browser* dials. In compose this is the "
            "host-visible ws://localhost:7880, because the token is used from outside the container network."
        ),
    )
    internal_url: str | None = Field(
        default=None,
        description=(
            "The URL the API process itself uses to reach LiveKit's management API, for the readiness probe. "
            "Defaults to `url`. They differ whenever the browser and this service sit on different networks: "
            "in compose the browser dials localhost while the API reaches the server by its service name."
        ),
    )
    api_key: str = Field(
        default="devkey",
        min_length=1,
        description="LiveKit API key. 'devkey' is what livekit-server --dev issues.",
    )
    api_secret: SecretStr = Field(
        default=SecretStr("secret"),
        description="LiveKit API secret. 'secret' is what livekit-server --dev issues.",
    )
    token_ttl_minutes: int = Field(
        default=15,
        gt=0,
        le=1440,
        description="Lifetime of a join token. Short by design: it only has to survive joining.",
    )

    model_config = SettingsConfigDict(env_prefix="LIVEKIT_", env_file=".env", extra="ignore")


@lru_cache(maxsize=1)
def get_livekit_settings() -> LiveKitSettings:
    return LiveKitSettings()
