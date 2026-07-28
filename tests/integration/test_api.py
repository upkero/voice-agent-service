"""The HTTP surface, through the real app.

Nothing is stubbed: real middleware, real routing, real error handling. What is
checked here is the wiring, which unit tests of the service cannot see.
"""

import jwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from src.app.api.v1.dependencies import get_livekit_gateway
from tests.fakes import FakeLiveKitGateway

SECRET = "test-livekit-secret-value-at-least-32-bytes"


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
    assert body["livekit_url"] == "ws://livekit-test:7880"


async def test_a_requested_room_is_honoured(client: AsyncClient) -> None:
    response = await client.post("/api/v1/token", json={"participant_name": "Anna", "room_name": "table-7"})

    assert response.status_code == 200
    assert response.json()["room_name"] == "table-7"


async def test_a_bad_room_name_is_a_typed_error_envelope(client: AsyncClient) -> None:
    response = await client.post("/api/v1/token", json={"room_name": "not a room"})

    assert response.status_code == 422
    assert response.json()["error_code"] == "invalid_room_name"


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
