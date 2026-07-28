import os

# Set before importing anything from src: settings are read at import time and
# then cached, and environment variables take priority over any local .env file.
os.environ.setdefault("OPS_CORE_API_KEY", "test-core-api-key-1234567890")
os.environ.setdefault("LIVEKIT_API_KEY", "devkey")
os.environ.setdefault("LIVEKIT_API_SECRET", "test-livekit-secret-value-at-least-32-bytes")
os.environ.setdefault("LIVEKIT_URL", "ws://livekit-test:7880")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("TOKEN_RATE_LIMIT_PER_MINUTE", "5")

from collections.abc import AsyncGenerator, Iterator  # noqa: E402
from datetime import date  # noqa: E402

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from src.app.api.v1.middleware.rate_limit import reset_rate_limit  # noqa: E402
from src.app.core.settings.agent import AgentSettings  # noqa: E402
from src.app.services.booking.ranking import NearestTimeRanking  # noqa: E402
from src.app.services.booking.reservation_service import ReservationService  # noqa: E402
from src.app.services.dialog.session_state import DialogSessionState  # noqa: E402
from src.app.services.dialog.tools import BookingTools  # noqa: E402
from src.main import create_app  # noqa: E402
from tests.fakes import FakeBookingGateway  # noqa: E402

TODAY = date(2026, 7, 23)


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> Iterator[None]:
    """Counters are process-global; keep cases independent."""
    reset_rate_limit()
    yield
    reset_rate_limit()


@pytest.fixture
def today() -> date:
    return TODAY


@pytest.fixture
def repository() -> FakeBookingGateway:
    return FakeBookingGateway()


@pytest.fixture
def state() -> DialogSessionState:
    return DialogSessionState("room-under-test")


@pytest.fixture
def reservations(repository: FakeBookingGateway) -> ReservationService:
    return ReservationService(repository, NearestTimeRanking())


@pytest.fixture
def agent_settings() -> AgentSettings:
    return AgentSettings(language="en")


@pytest.fixture
def tools(
    reservations: ReservationService,
    state: DialogSessionState,
    agent_settings: AgentSettings,
) -> BookingTools:
    return BookingTools(reservations, state, agent_settings, today=lambda: TODAY)


@pytest.fixture
def app() -> Iterator[FastAPI]:
    """The real app with the real middleware, routing and error handling.

    Nothing is stubbed: the token service signs with a test secret and needs no
    outside dependency, so these tests exercise the wiring rather than a mock
    of it.
    """
    from src.app.bootstrap.container import ApplicationContainer

    application = create_app()
    application.state.container = ApplicationContainer()
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as async_client:
        yield async_client
