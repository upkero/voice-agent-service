from datetime import UTC, datetime

import jwt
import pytest
from pydantic import SecretStr

from src.app.core.settings.livekit import LiveKitSettings
from src.app.gateways.livekit_probe import to_http_url
from src.app.services.token.access_token_service import AccessTokenService, InvalidRoomNameError

SECRET = "test-livekit-secret-value-at-least-32-bytes"


@pytest.fixture
def service() -> AccessTokenService:
    return AccessTokenService(
        LiveKitSettings(
            url="ws://livekit-test:7880",
            api_key="devkey",
            api_secret=SecretStr(SECRET),
            token_ttl_minutes=15,
        )
    )


def _claims(token: str) -> dict:
    return jwt.decode(token, SECRET, algorithms=["HS256"])


def test_the_token_grants_joining_one_named_room(service) -> None:
    issued = service.issue("Anna", room_name="table-42")

    grants = _claims(issued.token)["video"]
    assert grants["roomJoin"] is True
    assert grants["room"] == "table-42"
    assert issued.room_name == "table-42"


def test_the_token_grants_nothing_administrative(service) -> None:
    """It is minted without a credential, so it must not be able to create
    rooms, list them or administer anything."""
    grants = _claims(service.issue("Anna").token)["video"]

    for forbidden in ("roomCreate", "roomAdmin", "roomList", "roomRecord"):
        assert not grants.get(forbidden), forbidden


def test_data_publishing_is_granted(service) -> None:
    """The degraded path speaks over the data channel; a token that cannot
    receive it would turn a degraded call into a silent one."""
    assert _claims(service.issue("Anna").token)["video"]["canPublishData"] is True


def test_an_omitted_room_gets_a_private_one(service) -> None:
    first = service.issue("Anna")
    second = service.issue("Boris")

    assert first.room_name != second.room_name
    assert first.room_name.startswith("booking-")


def test_identities_are_unique_for_identical_names(service) -> None:
    """LiveKit treats a repeated identity as the same participant reconnecting,
    so two guests sharing one would evict each other."""
    first = service.issue("guest")
    second = service.issue("guest")

    assert first.participant_name != second.participant_name


def test_a_non_ascii_name_still_yields_a_usable_identity(service) -> None:
    issued = service.issue("Мила")

    assert issued.participant_name.startswith("guest-")
    # The name the guest gave survives for display, even though the identity is
    # reduced to something safe for URLs and logs.
    assert _claims(issued.token)["name"] == "Мила"


@pytest.mark.parametrize("bad", ["has space", "semi;colon", "slash/es", "quote'", "../escape"])
def test_hostile_room_names_are_refused(service, bad) -> None:
    with pytest.raises(InvalidRoomNameError):
        service.issue("Anna", room_name=bad)


def test_the_token_expires(service) -> None:
    issued = service.issue("Anna")
    claims = _claims(issued.token)

    assert claims["exp"] > datetime.now(UTC).timestamp()
    assert issued.expires_at > datetime.now(UTC)


def test_the_url_is_handed_back_for_the_client_to_connect_to(service) -> None:
    assert service.issue("Anna").livekit_url == "ws://livekit-test:7880"


@pytest.mark.parametrize(
    ("signalling", "management"),
    [
        ("ws://livekit:7880", "http://livekit:7880"),
        ("wss://example.livekit.cloud", "https://example.livekit.cloud"),
        ("http://already-http:7880", "http://already-http:7880"),
    ],
)
def test_the_management_url_is_derived_from_the_signalling_one(signalling, management) -> None:
    """One setting rather than two, because two eventually disagree."""
    assert to_http_url(signalling) == management
