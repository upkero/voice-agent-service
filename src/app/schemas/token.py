from datetime import datetime

from pydantic import BaseModel, Field

from src.app.contracts.token import AccessTokenDTO
from src.app.core.settings.agent import AgentLanguage


class TokenRequest(BaseModel):
    participant_name: str = Field(
        default="guest",
        max_length=64,
        description="Display name for the caller. Not an identity: the service derives a unique one.",
    )
    room_name: str | None = Field(
        default=None,
        max_length=64,
        description="Room to join. Omit to have a fresh private room generated.",
    )
    language: AgentLanguage | None = Field(
        default=None,
        description="Language of this call. Omit to use the service's AGENT_LANGUAGE.",
    )


class TokenResponse(BaseModel):
    token: str
    room_name: str
    participant_name: str
    livekit_url: str
    expires_at: datetime
    language: AgentLanguage

    @classmethod
    def from_contract(cls, access: AccessTokenDTO) -> "TokenResponse":
        # Contract -> schema conversion lives on the schema, so routers stay
        # thin and no contract ever leaks out as a response by accident.
        return cls(
            token=access.token,
            room_name=access.room_name,
            participant_name=access.participant_name,
            livekit_url=access.livekit_url,
            expires_at=access.expires_at,
            language=access.language,  # type: ignore[arg-type]
        )
