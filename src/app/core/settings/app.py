from functools import lru_cache
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class AppSettings(BaseSettings):
    """Settings for the HTTP process only.

    There is no DB_URL here: this service owns no database. Its only data
    source is ops-core-api over HTTP, configured by CoreApiSettings.
    """

    # NoDecode stops the settings source from JSON-decoding this field, which
    # it does for any complex type before validators run. Without it a plain
    # "a,b" env value fails at parse time and split_comma_separated never sees it.
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default=[],
        description="Allowed browser origins, comma-separated in the environment. Empty means no browser may call this.",
    )
    token_rate_limit_per_minute: int = Field(
        default=20,
        gt=0,
        description="Per-IP limit on POST /token. It mints room-join JWTs without a key, so it stays bounded.",
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def split_comma_separated(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache(maxsize=1)
def get_app_settings() -> AppSettings:
    return AppSettings()
