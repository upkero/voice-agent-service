from datetime import date, time

import pytest

from src.app.exceptions.booking import CoreUnavailableError
from src.app.services.booking.ranking import NearestTimeRanking
from src.app.services.booking.reservation_service import MAX_OFFERS, ReservationService
from src.app.services.dialog.session_state import DialogSessionState
from tests.fakes import CODE_KEY, TOMORROW, FakeBookingGateway, UnavailableBookingGateway, make_slot, seeded_slots


async def test_offers_the_nearest_times_to_the_request(
    reservations: ReservationService, state: DialogSessionState
) -> None:
    """The demo phrase: a table for four at 19:00, against the real seed data.

    There is no 19:00 slot. Answering "no" would be correct and useless; the
    job is to come back with 19:30 and 18:00, in that order.
    """
    offers = await reservations.find_offers(state, TOMORROW, party_size=4, preferred_time=time(19, 0))

    assert [offer.slot.slot_time.strftime("%H:%M") for offer in offers] == ["19:30", "18:00", "13:30"]


async def test_offers_exclude_tables_that_are_too_small(
    reservations: ReservationService, state: DialogSessionState
) -> None:
    offers = await reservations.find_offers(state, TOMORROW, party_size=5, preferred_time=None)

    # Only the 19:30 table seats six; the 12:00 two-seater must not be offered.
    assert [offer.slot.capacity for offer in offers] == [6]


async def test_offers_are_capped(state: DialogSessionState) -> None:
    """A guest cannot hold a read-out list of six times in their head."""
    many = [make_slot(hour, 0, 6) for hour in range(12, 22)]
    service = ReservationService(FakeBookingGateway(many), NearestTimeRanking(), CODE_KEY)

    offers = await service.find_offers(state, TOMORROW, party_size=2, preferred_time=None)

    assert len(offers) == MAX_OFFERS


async def test_no_free_tables_returns_nothing_rather_than_failing(
    reservations: ReservationService, state: DialogSessionState
) -> None:
    offers = await reservations.find_offers(state, date(2026, 12, 25), party_size=2, preferred_time=None)

    assert offers == []


async def test_reserving_marks_the_slot_taken(
    reservations: ReservationService, state: DialogSessionState, repository: FakeBookingGateway
) -> None:
    offers = await reservations.find_offers(state, TOMORROW, party_size=4, preferred_time=time(19, 0))

    summary = await reservations.reserve(state, offers[0].ref, "Ivanov", 4)

    assert summary.guest_name == "Ivanov"
    assert summary.slot_time == time(19, 30)
    assert offers[0].slot.id in repository.taken


async def test_reserving_an_unoffered_reference_never_reaches_the_repository(
    reservations: ReservationService, state: DialogSessionState, repository: FakeBookingGateway
) -> None:
    """A hallucinated reference must fail locally, not be resolved remotely."""
    from src.app.exceptions.booking import UnknownReferenceError

    with pytest.raises(UnknownReferenceError):
        await reservations.reserve(state, "slot_7", "Ivanov", 4)

    assert repository.calls == []


async def test_core_outage_surfaces_as_a_typed_failure(state: DialogSessionState) -> None:
    service = ReservationService(UnavailableBookingGateway(), NearestTimeRanking(), CODE_KEY)

    with pytest.raises(CoreUnavailableError):
        await service.find_offers(state, TOMORROW, party_size=2, preferred_time=None)


async def test_a_new_availability_check_invalidates_the_previous_offers(
    reservations: ReservationService, state: DialogSessionState
) -> None:
    """Offers describe one answer; keeping stale ones lets a guest accept a
    table that was withdrawn two questions ago."""
    first = await reservations.find_offers(state, TOMORROW, party_size=6, preferred_time=None)
    assert [offer.ref for offer in first] == ["slot_1"]

    await reservations.find_offers(state, TOMORROW, party_size=2, preferred_time=time(12, 0))

    assert state.resolve_slot("slot_1").slot_time == time(12, 0)


async def test_find_existing_returns_speakable_summaries(
    reservations: ReservationService, state: DialogSessionState
) -> None:
    offers = await reservations.find_offers(state, TOMORROW, party_size=4, preferred_time=None)
    await reservations.reserve(state, offers[0].ref, "Petrova", 4)

    found = await reservations.find_existing(DialogSessionState("later-call"), "petrova", TOMORROW)

    assert len(found) == 1
    assert found[0].guest_name == "Petrova"
    assert found[0].slot_date == TOMORROW


async def test_seeded_slots_match_ops_core_api() -> None:
    """Guards the fixture itself against drifting from the real seed data."""
    assert [slot.slot_time.strftime("%H:%M") for slot in seeded_slots()] == ["12:00", "13:30", "18:00", "19:30"]
    assert [slot.capacity for slot in seeded_slots()] == [2, 4, 4, 6]
