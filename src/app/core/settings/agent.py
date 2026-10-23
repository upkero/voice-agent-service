from datetime import date, datetime
from functools import lru_cache
from typing import Literal, get_args
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AgentLanguage = Literal["ru", "en"]


class AgentSettings(BaseSettings):
    """The persona and the language she works in.

    Language is one knob, not three: STT_LANGUAGE and TTS_VOICE may override it
    independently, but leaving them unset keeps the pipeline coherent instead
    of transcribing Russian and answering in an American voice.
    """

    language: AgentLanguage = Field(
        default="ru",
        description="Conversation language. Drives the STT hint, the TTS voice and the persona strings.",
    )
    name: str = Field(
        default="Мила",
        min_length=1,
        max_length=40,
        description="What the agent calls herself.",
    )
    name_en: str = Field(
        default="Mila",
        min_length=1,
        max_length=40,
        description="What she calls herself on an English call: a Cyrillic name read by an English voice is mangled.",
    )
    venue_name: str = Field(
        default="Aurora",
        min_length=1,
        max_length=80,
        description="Restaurant she answers for.",
    )
    max_party_size: int = Field(
        default=12,
        ge=1,
        le=100,
        description="Largest party she will take by voice. Bigger groups go to a human.",
    )
    booking_horizon_days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="How far ahead a table may be booked.",
    )
    tool_wait_seconds: float = Field(
        default=8.0,
        gt=0,
        le=30,
        description="Longest a guest waits on one booking tool, retries included, before hearing it is unavailable.",
    )

    timezone: str = Field(
        default="Europe/Moscow",
        description=(
            "IANA zone of the restaurant: what 'today' means on a call. Keep it equal to ops-core-api's "
            "BUSINESS_TIMEZONE. The server clock is UTC, so without it 'today' is wrong for three hours a night."
        ),
    )

    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env", extra="ignore")

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    def today(self) -> date:
        """The restaurant's date right now, not the server's."""
        return datetime.now(ZoneInfo(self.timezone)).date()

    @property
    def display_name(self) -> str:
        """The name she gives on this call, in the script the call's voice can say."""
        return self.name_en if self.language == "en" else self.name

    def for_language(self, language: str | None) -> "AgentSettings":
        """This call's settings: the configured ones with the guest's language on top.

        Anything that is not a supported language — absent, empty, a typo in a
        hand-made token — falls back to AGENT_LANGUAGE rather than failing the call.
        """
        if language not in get_args(AgentLanguage) or language == self.language:
            return self
        return self.model_copy(update={"language": language})


@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    return AgentSettings()
