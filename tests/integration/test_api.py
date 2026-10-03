"""The HTTP surface, through the real app.

Nothing is stubbed: real middleware, real routing, real error handling. What is
checked here is the wiring, which unit tests of the service cannot see.
"""

from uuid import UUID

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.app.api.v1.dependencies import get_livekit_gateway
from src.app.bootstrap.container import ApplicationContainer
from src.app.core.settings.livekit import get_livekit_settings
from tests.fakes import FakeLiveKitGateway

# Read from settings rather than repeating the literal from conftest. conftest
# sets the test values with os.environ.setdefault, which is a no-op when the
# variable is already exported — CI does export LIVEKIT_API_SECRET. A literal
# here would then be verifying a token against a secret the app never used.
SECRET = get_livekit_settings().api_secret.get_secret_value()


async def test_liveness_is_open_and_cheap(client: AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_is_ok_when_livekit_answers(app: FastAPI, client: AsyncClient) -> None:
    app.dependency_overrides[get_livekit_gateway] = lambda: FakeLiveKitGateway(reachable=True)

    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_is_503_when_livekit_is_unreachable(app: FastAPI, client: AsyncClient) -> None:
    """A token for a room nobody can join is worse than an honest 503."""
    app.dependency_overrides[get_livekit_gateway] = lambda: FakeLiveKitGateway(reachable=False)

    response = await client.get("/health/ready")

    assert response.status_code == 503


async def test_a_token_can_be_issued_with_no_body_fields(client: AsyncClient) -> None:
    response = await client.post("/api/v1/token", json={})

    assert response.status_code == 200
    body = response.json()
    claims = jwt.decode(body["token"], SECRET, algorithms=["HS256"])
    assert claims["video"]["room"] == body["room_name"]
    assert body["livekit_url"] == get_livekit_settings().url


@pytest.mark.parametrize("requested", ["table-7", "not a room"])
async def test_a_requested_room_is_ignored(client: AsyncClient, requested: str) -> None:
    """The endpoint is unauthenticated: honouring a name would let anyone who
    knows it join that call with publish and subscribe rights."""
    response = await client.post("/api/v1/token", json={"participant_name": "Anna", "room_name": requested})

    assert response.status_code == 200
    assert response.json()["room_name"].startswith("booking-")


async def test_an_oversized_name_is_rejected_by_the_schema(client: AsyncClient) -> None:
    response = await client.post("/api/v1/token", json={"participant_name": "x" * 200})

    assert response.status_code == 422
    assert response.json()["error_code"] == "request_validation_error"


async def test_the_request_id_is_echoed(client: AsyncClient) -> None:
    response = await client.post("/api/v1/token", json={}, headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"


async def test_the_token_endpoint_is_rate_limited(client: AsyncClient) -> None:
    """It mints signed credentials without asking for one, so it stays bounded."""
    statuses = [(await client.post("/api/v1/token", json={})).status_code for _ in range(7)]

    assert 429 in statuses
    limited = next(code for code in statuses if code == 429)
    assert limited == 429


async def test_a_rate_limited_response_says_when_to_retry(client: AsyncClient) -> None:
    for _ in range(6):
        response = await client.post("/api/v1/token", json={})

    assert response.status_code == 429
    assert response.json()["error_code"] == "rate_limit_exceeded"
    assert "Retry-After" in response.headers


async def test_health_is_never_rate_limited(client: AsyncClient) -> None:
    """The container runtime polls it; throttling it restarts a healthy service."""
    for _ in range(20):
        response = await client.get("/health/live")

    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/api/v1/bookings", "/api/v1/chat"])
async def test_the_service_exposes_nothing_beyond_tokens_and_health(client: AsyncClient, path: str) -> None:
    """The dialogue lives in the worker. An HTTP booking endpoint here would be
    a second, untested way to take a table."""
    assert (await client.post(path, json={})).status_code == 404


async def test_the_token_reports_the_language_of_the_call(client: AsyncClient) -> None:
    english = await client.post("/api/v1/token", json={"language": "en"})
    default = await client.post("/api/v1/token", json={})

    assert english.json()["language"] == "en"
    assert default.json()["language"] in {"ru", "en"}  # whatever AGENT_LANGUAGE says


async def test_an_unsupported_language_is_refused(client: AsyncClient) -> None:
    response = await client.post("/api/v1/token", json={"language": "de"})

    assert response.status_code == 422


async def test_a_form_body_is_a_validation_error_not_a_crash(client: AsyncClient) -> None:
    # curl's default content type when -H 'Content-Type: application/json' is
    # forgotten. Pydantic keeps the raw bytes in the error, undecodable ones too.
    for body in (b"room_name=table-7", b"\xff\xfe"):
        response = await client.post(
            "/api/v1/token",
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        assert response.status_code == 422
        assert response.json()["error_code"] == "request_validation_error"


async def test_an_unhandled_error_is_a_500_that_still_carries_the_request_id(app: FastAPI) -> None:
    async def boom() -> None:
        raise RuntimeError("boom")

    app.add_api_route("/boom", boom)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as raw_client:
        response = await raw_client.get("/boom", headers={"X-Request-ID": "abc-123"})

    assert response.status_code == 500
    assert response.json()["error_code"] == "internal_server_error"
    assert response.headers["X-Request-ID"] == "abc-123"


async def test_a_malformed_request_id_is_replaced(client: AsyncClient) -> None:
    # Echoed, forwarded upstream and logged, so a markup or oversized id is replaced.
    for bad in ("attacker-<script>", "r" * 129):
        response = await client.get("/no-such-route", headers={"X-Request-ID": bad})

        assert UUID(response.headers["X-Request-ID"])


async def test_a_misconfiguration_fails_the_boot_not_the_first_request(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A container property that cannot be built stands in for any misconfiguration.
    def broken(self: ApplicationContainer) -> None:
        raise RuntimeError("misconfigured")

    monkeypatch.setattr(ApplicationContainer, "access_token_service", property(broken))

    with pytest.raises(RuntimeError, match="misconfigured"):
        async with app.router.lifespan_context(app):
            pass
