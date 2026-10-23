from datetime import date, time
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest

from src.app.core.settings.core_api import CoreApiSettings
from src.app.exceptions.booking import CoreUnavailableError, WrongBookingCodeError
from src.app.gateways.core_api_booking import CoreApiBookingGateway
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


async def test_tables_sharing_a_time_are_offered_once_as_the_smallest_that_fits(state: DialogSessionState) -> None:
    """Three free tables at 20:00 are one offer, not "eight, eight or eight"."""
    slots = [make_slot(20, 0, 8), make_slot(20, 0, 4), make_slot(20, 0, 6), make_slot(18, 0, 4)]
    service = ReservationService(FakeBookingGateway(slots), NearestTimeRanking(), CODE_KEY)

    offers = await service.find_offers(state, TOMORROW, party_size=4, preferred_time=time(20, 0))

    assert [(offer.slot.slot_time.hour, offer.slot.capacity) for offer in offers] == [(20, 4), (18, 4)]


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


async def _book(reservations: ReservationService, name: str) -> None:
    session = DialogSessionState(f"call-{name}")
    offers = await reservations.find_offers(session, TOMORROW, party_size=2, preferred_time=None)
    await reservations.reserve(session, offers[0].ref, name, 2)


async def test_find_existing_returns_only_the_matching_booking_of_a_busy_date(
    reservations: ReservationService,
) -> None:
    """The whole day is fetched to match the name, but no other guest may come out of it."""
    for name in ("Андрей Иванов", "Алексей Петров", "Мария Сидорова"):
        await _book(reservations, name)

    found = await reservations.find_existing(DialogSessionState("later-call"), "Андрея", TOMORROW)

    assert [booking.guest_name for booking in found] == ["Андрей Иванов"]


async def test_find_existing_survives_the_name_drift_between_calls(reservations: ReservationService) -> None:
    await _book(reservations, "Мария")

    found = await reservations.find_existing(DialogSessionState("later-call"), "Марии", TOMORROW)

    assert [booking.guest_name for booking in found] == ["Мария"]


async def test_find_existing_with_no_match_returns_nothing_and_keeps_other_dates_out(
    reservations: ReservationService, repository: FakeBookingGateway
) -> None:
    await _book(reservations, "Алексей")

    found = await reservations.find_existing(DialogSessionState("later-call"), "Андрей", TOMORROW)

    assert found == []
    # One lookup, for the date that was asked: a miss does not widen the search.
    assert repository.calls.count("list_active_bookings") == 1


async def test_the_booking_code_still_cancels_after_a_fuzzy_match(
    reservations: ReservationService, repository: FakeBookingGateway
) -> None:
    await _book(reservations, "Андрей")
    code = reservations.booking_code(next(iter(repository.bookings)))

    later = DialogSessionState("later-call")
    found = await reservations.find_existing(later, "Андрея", TOMORROW, code)

    assert len(found) == 1
    assert (await reservations.cancel(later, found[0].ref)).guest_name == "Андрей"


async def test_a_wrong_code_is_still_refused_for_a_fuzzy_match(
    reservations: ReservationService, repository: FakeBookingGateway
) -> None:
    await _book(reservations, "Андрей")
    code = reservations.booking_code(next(iter(repository.bookings)))
    wrong = f"{(int(code) + 1) % 10_000:04d}"

    with pytest.raises(WrongBookingCodeError):
        await reservations.find_existing(DialogSessionState("later-call"), "Андрея", TOMORROW, wrong)


async def test_every_page_of_the_day_is_searched() -> None:
    """The match is made here, so a booking on the last page must still be found."""
    ids = [uuid4() for _ in range(5)]
    seen_offsets: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert "guest_name" not in request.url.params
        offset = int(request.url.params["offset"])
        seen_offsets.append(offset)
        items = [
            {"id": str(ids[i]), "guest_name": f"Гость {i}" if i != 4 else "Андрей", "slot_id": str(uuid4()),
             "party_size": 2, "status": "active"}
            for i in range(offset, min(offset + 2, 5))
        ]  # fmt: skip
        return httpx.Response(200, json={"items": items, "total": 5, "limit": 2, "offset": offset})

    gateway = CoreApiBookingGateway(
        CoreApiSettings(api_key="test-key-1234567890"),
        httpx.AsyncClient(transport=httpx.MockTransport(respond), base_url="http://core.test"),
    )
    service = ReservationService(gateway, NearestTimeRanking(), CODE_KEY)

    with patch("src.app.gateways.core_api_booking._SLOT_PAGE_SIZE", 2):
        found = await service.find_existing(DialogSessionState("later-call"), "Андрея", TOMORROW)

    assert seen_offsets == [0, 2, 4]
    assert [booking.guest_name for booking in found] == ["Андрей"]
