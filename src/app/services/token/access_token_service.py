"""Minting LiveKit join tokens.

The whole HTTP surface of this service exists to hand a browser one of these.
The browser then talks WebRTC to the LiveKit server directly, and the agent
worker meets it in the room — no audio ever passes through the API process,
which is why that process stays small enough to be uninteresting.
"""

import re
import secrets
from datetime import UTC, datetime, timedelta

from livekit import api

from src.app.contracts.token import AccessTokenDTO
from src.app.core.settings.livekit import LiveKitSettings
from src.app.exceptions.base import BaseAppException

# LiveKit accepts a fairly free-form room name, but anything that travels in a
# URL and lands in logs is worth pinning down. Letters, digits, dash and
# underscore only.
_ROOM_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PARTICIPANT_NAME_MAX = 64


class InvalidRoomNameError(BaseAppException):
    status_code = 422
    error_code = "invalid_room_name"
    default_detail = "Room name may contain only letters, digits, dashes and underscores."


class AccessTokenService:
    def __init__(self, settings: LiveKitSettings) -> None:
        self._settings = settings

    def issue(self, participant_name: str, room_name: str | None = None) -> AccessTokenDTO:
        room = self._resolve_room_name(room_name)
        identity = self._resolve_identity(participant_name)
        expires_at = datetime.now(UTC) + timedelta(minutes=self._settings.token_ttl_minutes)

        token = (
            api.AccessToken(self._settings.api_key, self._settings.api_secret.get_secret_value())
            .with_identity(identity)
            .with_name(participant_name.strip() or identity)
            .with_ttl(timedelta(minutes=self._settings.token_ttl_minutes))
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room,
                    can_publish=True,
                    can_subscribe=True,
                    # The data channel is how the agent explains itself when its
                    # voice is unavailable, and a token that cannot receive it
                    # would turn a degraded call into a silent one.
                    can_publish_data=True,
                    # Not granted: room_create, room_admin, room_list. A guest
                    # needs to join one room, and a token minted without
                    # authentication should be able to do nothing else.
                )
            )
        )

        return AccessTokenDTO(
            token=token.to_jwt(),
            room_name=room,
            participant_name=identity,
            livekit_url=self._settings.url,
            expires_at=expires_at,
        )

    @staticmethod
    def _resolve_room_name(room_name: str | None) -> str:
        if room_name is None or not room_name.strip():
            # A random name rather than a shared default: two guests who both
            # open the demo without naming a room should not end up in the same
            # conversation with the same agent.
            return f"booking-{secrets.token_urlsafe(9)}"
        candidate = room_name.strip()
        if not _ROOM_NAME_PATTERN.match(candidate):
            raise InvalidRoomNameError()
        return candidate

    @staticmethod
    def _resolve_identity(participant_name: str) -> str:
        """Identity must be unique per participant, so it is never the raw name.

        Two people called "guest" joining with the same identity would knock
        each other out of the room: LiveKit treats a duplicate identity as a
        reconnection of the same participant.

        Non-ASCII is stripped here and only here. An identity is a key that
        ends up in URLs, logs and metrics, so it stays boring; the name the
        guest actually gave is preserved verbatim via `with_name`, which is
        what any client displays.
        """
        cleaned = re.sub(r"[^A-Za-z0-9_-]", "", participant_name.strip())[:_PARTICIPANT_NAME_MAX]
        prefix = cleaned or "guest"
        return f"{prefix}-{secrets.token_urlsafe(6)}"
