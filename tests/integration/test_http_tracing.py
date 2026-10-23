"""The server span and metrics of voice-api's requests, on the production app.

These pin what the pinned FastAPI instrumentation gets wrong on this FastAPI
(see `_use_route_templates` in core/telemetry.py): a request whose method the
route does not allow used to raise inside it and answer 500.
"""

import pytest
from httpx import AsyncClient

from tests import telemetry

pytestmark = pytest.mark.usefixtures("spans")


@pytest.mark.parametrize("method", ["PUT", "OPTIONS"])
async def test_a_method_the_route_does_not_allow_is_a_405_not_a_crash(client: AsyncClient, method: str) -> None:
    response = await client.request(method, "/api/v1/token")

    assert response.status_code == 405


async def test_the_server_span_and_duration_metric_carry_the_route(client: AsyncClient) -> None:
    before = telemetry.reading("http.server.request.duration", **{"http.route": "/api/v1/token"})

    await client.put("/api/v1/token")

    (span,) = [s for s in telemetry.finished_spans() if s.kind.name == "SERVER"]
    assert (span.attributes or {})["http.route"] == "/api/v1/token"
    assert telemetry.reading("http.server.request.duration", **{"http.route": "/api/v1/token"}) == before + 1
