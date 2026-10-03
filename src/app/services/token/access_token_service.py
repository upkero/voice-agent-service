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

_PARTICIPANT_NAME_MAX = 64


class AccessTokenService:
    def __init__(self, settings: LiveKitSettings, default_language: str = "ru") -> None:
        self._settings = settings
        self._default_language = default_language

    def issue(self, participant_name: str, language: str | None = None) -> AccessTokenDTO:
        # Always a fresh random room, never one the caller names: this endpoint
        # is unauthenticated, so honouring a name would let anyone who knows or
        # guesses it join that call with publish and subscribe rights.
        room = f"booking-{secrets.token_urlsafe(9)}"
        identity = self._resolve_identity(participant_name)
        call_language = language or self._default_language
        expires_at = datetime.now(UTC) + timedelta(minutes=self._settings.token_ttl_minutes)

        token = (
            api.AccessToken(self._settings.api_key, self._settings.api_secret.get_secret_value())
            .with_identity(identity)
            .with_name(participant_name.strip() or identity)
            # How the agent worker learns the language of this call: it reads the
            # attribute off the participant once they join. Not a secret and not
            # authority — an unknown value just falls back to the configured one.
            .with_attributes({"language": call_language})
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
            language=call_language,
        )

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
