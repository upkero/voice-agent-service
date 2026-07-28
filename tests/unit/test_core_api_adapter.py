"""The adapter's own behaviour, driven through a stubbed transport.

Everything else in this suite talks to FakeBookingGateway, which is the point
of the port — but the mapping from HTTP to typed exceptions lives here and has no
other home. httpx.MockTransport is the smallest way to exercise it without a
server.
"""

from datetime import date

import httpx
import pytest

from src.app.core.settings.core_api import CoreApiSettings
from src.app.exceptions.booking import CoreRateLimitedError, CoreUnavailableError
from src.app.gateways.core_api_booking import CoreApiBookingGateway

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
