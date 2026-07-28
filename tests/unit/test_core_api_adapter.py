"""The adapter's own behaviour, driven through a stubbed transport.

Everything else in this suite talks to FakeBookingGateway, which is the point
of the port — but the mapping from HTTP to typed exceptions lives here and has no
other home. httpx.MockTransport is the smallest way to exercise it without a
server.
"""

from datetime import date

import httpx
import pytest

from src.app.core.request_id import set_request_id
from src.app.core.settings.core_api import CoreApiSettings
from src.app.exceptions.booking import CoreRateLimitedError, CoreUnavailableError
from src.app.gateways.core_api_booking import (
    CoreApiBookingGateway,
    _inject_request_id,
    create_booking_gateway,
)

SETTINGS = CoreApiSettings(api_key="test-key-1234567890", max_attempts=2)


def _repository(handler: httpx.MockTransport) -> CoreApiBookingGateway:
    client = httpx.AsyncClient(transport=handler, base_url="http://core.test")
    return CoreApiBookingGateway(SETTINGS, client)


async def test_exhausted_429_surfaces_as_429_with_retry_after() -> None:
    """A throttled upstream must not be reported as a broken one."""
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        # Retry-After 0 so the backoff override does not make the test wait.
        return httpx.Response(429, headers={"Retry-After": "0"}, json={"detail": "slow down"})

    repository = _repository(httpx.MockTransport(respond))
    with pytest.raises(CoreRateLimitedError) as caught:
        await repository.list_available_slots(date(2026, 7, 23), 2)

    assert calls == SETTINGS.max_attempts
    assert caught.value.status_code == 429
    assert caught.value.headers["Retry-After"] == "0"


async def test_exhausted_503_still_surfaces_as_unavailable() -> None:
    repository = _repository(httpx.MockTransport(lambda _: httpx.Response(503)))
    with pytest.raises(CoreUnavailableError):
        await repository.list_available_slots(date(2026, 7, 23), 2)


async def test_the_request_id_travels_to_ops_core() -> None:
    """Otherwise the two services' logs cannot be joined for one booking."""
    seen: list[str | None] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("X-Request-ID"))
        return httpx.Response(200, json={"items": []})

    set_request_id("room-table-7")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(respond),
        base_url="http://core.test",
        event_hooks={"request": [_inject_request_id]},
    )
    await CoreApiBookingGateway(SETTINGS, client).list_available_slots(date(2026, 7, 23), 2)

    assert seen == ["room-table-7"]


def test_the_factory_installs_the_request_id_hook() -> None:
    """The test above proves the hook works; this proves it is actually wired."""
    gateway = create_booking_gateway(SETTINGS)

    assert _inject_request_id in gateway._client.event_hooks["request"]  # noqa: SLF001
