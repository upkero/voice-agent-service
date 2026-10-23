"""The spans an incident is read from, and the log fields that lead to them.

Every case drives the real code: BookingTools over the real ops-core gateway,
whose client is built by the production factory, against a stubbed transport.
"""

import logging
from uuid import uuid4

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.app.core.settings.agent import AgentSettings
from src.app.core.settings.core_api import CoreApiSettings
from src.app.core.telemetry import guest_name_fp
from src.app.gateways.core_api_booking import create_booking_gateway
from src.app.services.booking.ranking import NearestTimeRanking
from src.app.services.booking.reservation_service import ReservationService
from src.app.services.dialog.session_state import DialogSessionState
from src.app.services.dialog.tools import BookingTools
from src.entrypoint import bind_call
from tests.fakes import CODE_KEY, TOMORROW

_tracer = trace.get_tracer(__name__)


async def test_find_booking_span_sits_under_the_tool_and_above_ops_core(spans: InMemorySpanExporter) -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = {
            "id": str(uuid4()),
            "guest_name": "Иванова",
            "slot_id": str(uuid4()),
            "party_size": 2,
            "status": "active",
        }
        return httpx.Response(200, json={"items": [item]})

    gateway = create_booking_gateway(
        CoreApiSettings(api_key="test-key-1234567890", base_url="http://core.test"), httpx.MockTransport(respond)
    )
    tools = BookingTools(
        ReservationService(gateway, NearestTimeRanking(), CODE_KEY),
        DialogSessionState("room-trace"),
        AgentSettings(language="en"),
        today=lambda: TOMORROW,
    )
    bind_call("trace")

    with _tracer.start_as_current_span("function_tool") as tool_span:
        result = await tools.find_booking({"guest_name": "Иванову", "booking_date": TOMORROW.isoformat()})
    await gateway.close()

    assert result["ok"] is True
    finished = spans.get_finished_spans()
    find = next(span for span in finished if span.name == "booking.find_booking")
    assert find.parent is not None
    assert find.parent.span_id == tool_span.get_span_context().span_id
    assert find.attributes is not None
    assert find.attributes["booking.guest_name"] == "Иванову"
    assert find.attributes["booking.results.count"] == 1
    assert find.attributes["booking.result"] == "ok"

    traceparent = seen[0].headers["traceparent"]
    assert traceparent.split("-")[1] == format(find.context.trace_id, "032x")
    assert seen[0].headers["X-Request-ID"] == "room-trace"

    assert finished, "no spans recorded"
    for span in finished:
        assert span.attributes is not None
        assert span.attributes.get("request_id") == "room-trace", span.name


async def test_the_booking_code_never_reaches_a_span(spans: InMemorySpanExporter, tools: BookingTools) -> None:
    await tools.find_booking({"guest_name": "Ivanov", "booking_date": TOMORROW.isoformat(), "booking_code": "1234"})

    for span in spans.get_finished_spans():
        assert "1234" not in (span.attributes or {}).values()


def test_guest_name_fp_matches_across_case_and_spacing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBS_FINGERPRINT_KEY", "test-fingerprint-key")

    assert guest_name_fp("  Иванова ") == guest_name_fp("ИВАНОВА")
    assert guest_name_fp("Иванова") != guest_name_fp("Иванову")
    assert len(guest_name_fp("Иванова")) == 12


def test_guest_name_fp_is_empty_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OBS_FINGERPRINT_KEY", raising=False)

    assert guest_name_fp("Иванова") == ""


def test_log_lines_carry_the_trace_they_were_written_in() -> None:
    from src.app.core.logging import _RequestIdFilter

    record = logging.LogRecord("t", logging.INFO, "", 0, "m", (), None)
    with _tracer.start_as_current_span("outer") as span:
        _RequestIdFilter().filter(record)

    assert record.__dict__["trace_id"] == format(span.get_span_context().trace_id, "032x")
    assert record.__dict__["span_id"] == format(span.get_span_context().span_id, "016x")

    outside = logging.LogRecord("t", logging.INFO, "", 0, "m", (), None)
    _RequestIdFilter().filter(outside)
    assert outside.__dict__["trace_id"] == outside.__dict__["span_id"] == ""


async def test_rejected_arguments_log_no_guest_name(tools: BookingTools, caplog: pytest.LogCaptureFixture) -> None:
    # Over max_length: the name itself is the rejected value.
    name = "Иванову " * 30
    with caplog.at_level(logging.WARNING):
        result = await tools.find_booking({"guest_name": name, "booking_date": TOMORROW.isoformat()})

    assert result["reason"] == "invalid_arguments"
    record = next(r for r in caplog.records if r.getMessage().startswith("Rejected tool arguments"))
    assert "Иванову" not in str(record.__dict__)
