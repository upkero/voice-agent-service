from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AccessTokenDTO:
    token: str
    room_name: str
    participant_name: str
    livekit_url: str
    expires_at: datetime
    language: str
