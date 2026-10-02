from functools import lru_cache
from typing import Literal, get_args

from pydantic import Field
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

    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env", extra="ignore")

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
