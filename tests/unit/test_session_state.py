"""References, and the identifiers they stand in for.

The property under test is blunt: an identifier must never be visible to the
model, and a reference the session did not issue must never resolve.
"""

import json
import re
from datetime import time

import pytest

from src.app.exceptions.booking import UnknownReferenceError
from tests.fakes import TOMORROW

UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


async def test_no_tool_result_ever_contains_an_identifier(tools) -> None:
    """Scans everything the model would see, across a whole booking flow."""
    availability = {"booking_date": TOMORROW.isoformat(), "party_size": 4, "preferred_time": "19:00"}
    seen = [
        await tools.check_availability(availability),
        await tools.create_booking(
            {"slot_ref": "slot_1", "guest_name": "Ivanov", "party_size": 4, "confirmed": True}
        ),
        await tools.find_booking({"guest_name": "Ivanov", "booking_date": TOMORROW.isoformat()}),
        await tools.cancel_booking({"booking_ref": "booking_1", "confirmed": True}),
    ]

    rendered = json.dumps(seen)
    assert not UUID_PATTERN.search(rendered), f"an identifier leaked into a tool result: {rendered}"


async def test_a_booking_made_in_this_call_is_cancellable_without_a_lookup(tools, repository) -> None:
    """The point of remembering the identifier: no second search, no name
    matching, no chance of reaching somebody else's table."""
    await tools.check_availability({"booking_date": TOMORROW.isoformat(), "party_size": 4, "preferred_time": "19:00"})
    await tools.create_booking({"slot_ref": "slot_1", "guest_name": "Ivanov", "party_size": 4, "confirmed": True})

    result = await tools.cancel_booking({"booking_ref": "booking_1", "confirmed": True})

    assert result["ok"] is True
    assert "find_bookings" not in repository.calls


async def test_an_unissued_slot_reference_raises_before_any_call(state, repository, reservations) -> None:
    with pytest.raises(UnknownReferenceError):
        await reservations.reserve(state, "slot_3", "Ivanov", 2)

    assert repository.calls == []


async def test_an_unissued_booking_reference_raises_before_any_call(state, repository, reservations) -> None:
    with pytest.raises(UnknownReferenceError):
        await reservations.cancel(state, "booking_4")

    assert repository.calls == []


async def test_references_survive_being_re_offered(state, reservations) -> None:
    """Booking references outlive an availability re-check; slot ones do not."""
    offers = await reservations.find_offers(state, TOMORROW, 4, preferred_time=time(19, 0))
    booked = await reservations.reserve(state, offers[0].ref, "Ivanov", 4)

    await reservations.find_offers(state, TOMORROW, 2, preferred_time=time(12, 0))

    assert state.summarise_booking(booked.ref).guest_name == "Ivanov"


async def test_cancelling_opens_a_new_intent(state) -> None:
    before = state.intent_seq
    state.close_intent()
    assert state.intent_seq == before + 1
