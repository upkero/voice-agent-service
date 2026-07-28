"""What the model is allowed to do, and what happens when it tries otherwise.

Every case here is a thing a language model has actually been observed to do:
invent a field, drop a required one, send a party size of forty, call the
booking tool before the guest agreed.
"""

from datetime import timedelta
from typing import Any

import pytest

from src.app.core.settings.agent import AgentSettings
from src.app.services.booking.ranking import NearestTimeRanking
from src.app.services.booking.reservation_service import ReservationService
from src.app.services.dialog.phrases import phrase
from src.app.services.dialog.session_state import DialogSessionState
from src.app.services.dialog.tools import TOOL_SCHEMAS, BookingTools
from tests.fakes import TOMORROW, FakeBookingGateway, UnavailableBookingGateway

VALID_AVAILABILITY = {"booking_date": TOMORROW.isoformat(), "party_size": 4, "preferred_time": "19:00"}


async def _offer(tools: BookingTools) -> None:
    await tools.check_availability(dict(VALID_AVAILABILITY))


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({**VALID_AVAILABILITY, "extra": "x"}, "an invented field"),
        ({"party_size": 4, "preferred_time": None}, "a missing date"),
        ({**VALID_AVAILABILITY, "booking_date": "tomorrow"}, "a date that is not a date"),
        ({**VALID_AVAILABILITY, "preferred_time": "7pm"}, "a time that is not 24-hour"),
        ({**VALID_AVAILABILITY, "party_size": 0}, "a party of nobody"),
        ({**VALID_AVAILABILITY, "party_size": -3}, "a negative party"),
        ({**VALID_AVAILABILITY, "party_size": "four"}, "a party size as a word"),
    ],
)
async def test_bad_availability_arguments_are_refused(
    tools: BookingTools, repository: FakeBookingGateway, payload: dict[str, Any], why: str
) -> None:
    result = await tools.check_availability(payload)

    assert result["ok"] is False, why
    assert result["reason"] == "invalid_arguments"
    assert result["say"], "a refusal must still give Мила something to say"
    assert repository.calls == []


async def test_a_party_larger_than_the_venue_takes_goes_to_a_human(
    tools: BookingTools, repository: FakeBookingGateway
) -> None:
    result = await tools.check_availability({**VALID_AVAILABILITY, "party_size": 40})

    assert result["reason"] == "party_too_large"
    assert repository.calls == []


async def test_a_date_beyond_the_horizon_is_refused_locally(
    tools: BookingTools, repository: FakeBookingGateway
) -> None:
    result = await tools.check_availability({**VALID_AVAILABILITY, "booking_date": "2027-06-01"})

    assert result["reason"] == "date_too_far"
    assert repository.calls == []


async def test_booking_without_agreement_never_reaches_the_repository(
    tools: BookingTools, repository: FakeBookingGateway
) -> None:
    """The gate that stops a misheard 'yes' from taking a table."""
    await _offer(tools)

    result = await tools.create_booking(
        {"slot_ref": "slot_1", "guest_name": "Ivanov", "party_size": 4, "confirmed": False}
    )

    assert result["reason"] == "not_confirmed"
    assert "create_booking" not in repository.calls


async def test_cancelling_without_agreement_never_reaches_the_repository(
    tools: BookingTools, repository: FakeBookingGateway
) -> None:
    await _offer(tools)
    await tools.create_booking({"slot_ref": "slot_1", "guest_name": "Ivanov", "party_size": 4, "confirmed": True})

    result = await tools.cancel_booking({"booking_ref": "booking_1", "confirmed": False})

    assert result["reason"] == "not_confirmed"
    assert "cancel_booking" not in repository.calls


async def test_an_invented_slot_reference_is_refused(tools: BookingTools, repository: FakeBookingGateway) -> None:
    await _offer(tools)

    result = await tools.create_booking(
        {"slot_ref": "slot_9", "guest_name": "Ivanov", "party_size": 4, "confirmed": True}
    )

    assert result["reason"] == "unknown_reference"
    assert "create_booking" not in repository.calls


async def test_a_malformed_reference_fails_the_schema_not_the_lookup(
    tools: BookingTools, repository: FakeBookingGateway
) -> None:
    await _offer(tools)

    result = await tools.create_booking(
        {"slot_ref": "the first one", "guest_name": "Ivanov", "party_size": 4, "confirmed": True}
    )

    assert result["reason"] == "invalid_arguments"
    assert "create_booking" not in repository.calls


async def test_a_blank_guest_name_is_refused(tools: BookingTools) -> None:
    await _offer(tools)

    result = await tools.create_booking({"slot_ref": "slot_1", "guest_name": "", "party_size": 4, "confirmed": True})

    assert result["reason"] == "invalid_arguments"


async def test_an_outage_becomes_a_sentence_rather_than_an_exception(
    agent_settings: AgentSettings, state: DialogSessionState
) -> None:
    """A tool that raises is dead air, and dead air reads as a dropped call."""
    # Inject the fixed clock like the shared `tools` fixture, so TOMORROW stays
    # a future date whatever the real calendar says when the suite runs.
    tools = BookingTools(
        ReservationService(UnavailableBookingGateway(), NearestTimeRanking()),
        state,
        agent_settings,
        today=lambda: TOMORROW - timedelta(days=1),
    )

    result = await tools.check_availability(dict(VALID_AVAILABILITY))

    assert result["ok"] is False
    assert result["reason"] == "core_unavailable"
    assert result["say"] == phrase("en", "core_unavailable")


async def test_no_free_tables_is_a_success_with_an_explanation(tools: BookingTools) -> None:
    """A day inside the booking horizon that simply holds no free tables.

    Distinct from a refusal: there is nothing wrong with the request, so the
    call succeeds with an empty list and something to say about it.
    """
    empty_day = (TOMORROW + timedelta(days=3)).isoformat()

    result = await tools.check_availability({"booking_date": empty_day, "party_size": 2, "preferred_time": None})

    assert result["ok"] is True
    assert result["options"] == []
    assert result["say"]


async def test_every_schema_is_closed_and_fully_required() -> None:
    """The property that makes the interface deterministic rather than roughly right."""
    for schema in TOOL_SCHEMAS:
        params = schema["parameters"]
        assert params["additionalProperties"] is False, schema["name"]
        assert set(params["required"]) == set(params["properties"]), schema["name"]
        assert schema.get("description"), schema["name"]


async def test_every_failure_reason_has_a_phrase_in_both_languages() -> None:
    """A missing phrase is a mute agent, so it is a test failure instead."""
    from src.app.services.dialog.phrases import ERROR_PHRASES

    assert set(ERROR_PHRASES["ru"]) == set(ERROR_PHRASES["en"])
    assert all(text.strip() for table in ERROR_PHRASES.values() for text in table.values())
