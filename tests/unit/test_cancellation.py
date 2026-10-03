"""Cancelling, and what it frees.

A cancellation that leaves the table marked taken is worse than no cancellation
at all: the guest is gone and the seat stays empty all evening.
"""

from datetime import time

import pytest

from src.app.exceptions.booking import (
    BookingCodeAttemptsExhaustedError,
    BookingNotFoundError,
    NotCancellableError,
    WrongBookingCodeError,
)
from src.app.services.booking.reservation_service import ReservationService
from src.app.services.dialog.session_state import DialogSessionState
from tests.fakes import TOMORROW, FakeBookingGateway


async def test_cancelling_puts_the_table_back_on_offer(
    reservations: ReservationService, state: DialogSessionState
) -> None:
    offers = await reservations.find_offers(state, TOMORROW, 6, preferred_time=time(19, 30))
    booked = await reservations.reserve(state, offers[0].ref, "Ivanov", 6)

    assert await reservations.find_offers(state, TOMORROW, 6, preferred_time=None) == []

    await reservations.cancel(state, booked.ref)
    again = await reservations.find_offers(state, TOMORROW, 6, preferred_time=None)

    assert [offer.slot.slot_time for offer in again] == [time(19, 30)]


async def test_cancelling_twice_is_harmless(
    reservations: ReservationService, state: DialogSessionState, repository: FakeBookingGateway
) -> None:
    """A dropped call retries the cancellation; the caller should not have to
    tell 'I cancelled it' from 'I cancelled it twice'."""
    offers = await reservations.find_offers(state, TOMORROW, 4, preferred_time=None)
    booked = await reservations.reserve(state, offers[0].ref, "Ivanov", 4)

    first = await reservations.cancel(state, booked.ref)
    second = await reservations.cancel(state, booked.ref)

    assert first.ref == second.ref
    cancelled = [b for b in repository.bookings.values() if b.status.value == "cancelled"]
    assert len(cancelled) == 1


async def test_cancelling_an_unknown_booking_fails_clearly(
    reservations: ReservationService, state: DialogSessionState, repository: FakeBookingGateway
) -> None:
    offers = await reservations.find_offers(state, TOMORROW, 4, preferred_time=None)
    booked = await reservations.reserve(state, offers[0].ref, "Ivanov", 4)
    booking_id = state.resolve_booking(booked.ref).id
    del repository.bookings[booking_id]

    with pytest.raises(BookingNotFoundError):
        await reservations.cancel(state, booked.ref)


async def test_a_booking_found_by_name_and_date_cannot_be_cancelled(
    reservations: ReservationService, state: DialogSessionState, repository: FakeBookingGateway
) -> None:
    """A name and a date are guessable, so they prove nothing about who is calling.

    Anyone could ring up and say "cancel Anna's table on Saturday". Only a
    booking made during this call — whose reference this session issued — can
    be cancelled by voice.
    """
    offers = await reservations.find_offers(state, TOMORROW, 4, preferred_time=None)
    await reservations.reserve(state, offers[0].ref, "Petrova", 4)

    stranger = DialogSessionState("second-call")
    found = await reservations.find_existing(stranger, "Petrova", TOMORROW)

    with pytest.raises(NotCancellableError):
        await reservations.cancel(stranger, found[0].ref)
    assert all(b.status.value == "active" for b in repository.bookings.values())


async def test_a_cancelled_booking_is_not_found_again(
    reservations: ReservationService, state: DialogSessionState
) -> None:
    offers = await reservations.find_offers(state, TOMORROW, 4, preferred_time=None)
    booked = await reservations.reserve(state, offers[0].ref, "Petrova", 4)
    await reservations.cancel(state, booked.ref)

    found = await reservations.find_existing(state, "Petrova", TOMORROW)

    assert found == []


async def _booked_on_an_earlier_call(reservations: ReservationService) -> str:
    first_call = DialogSessionState("first-call")
    offers = await reservations.find_offers(first_call, TOMORROW, 4, preferred_time=None)
    booked = await reservations.reserve(first_call, offers[0].ref, "Petrova", 4)
    assert booked.code is not None
    return booked.code


async def test_a_booking_comes_with_a_short_spoken_code(reservations: ReservationService) -> None:
    code = await _booked_on_an_earlier_call(reservations)

    assert len(code) == 4
    assert code.isdigit()


async def test_the_booking_code_lets_a_later_call_cancel(
    reservations: ReservationService, repository: FakeBookingGateway
) -> None:
    code = await _booked_on_an_earlier_call(reservations)

    later_call = DialogSessionState("second-call")
    found = await reservations.find_existing(later_call, "Petrova", TOMORROW, code)
    cancelled = await reservations.cancel(later_call, found[0].ref)

    assert cancelled.guest_name == "Petrova"
    assert all(b.status.value == "cancelled" for b in repository.bookings.values())


async def test_a_wrong_code_is_refused_and_guessing_is_capped(reservations: ReservationService) -> None:
    code = await _booked_on_an_earlier_call(reservations)
    wrong = f"{(int(code) + 1) % 10000:04d}"
    stranger = DialogSessionState("second-call")

    for _ in range(3):
        with pytest.raises(WrongBookingCodeError):
            await reservations.find_existing(stranger, "Petrova", TOMORROW, wrong)

    # Even the right code no longer works on this call: otherwise three
    # attempts would be a speed limit, not a cap.
    with pytest.raises(BookingCodeAttemptsExhaustedError):
        await reservations.find_existing(stranger, "Petrova", TOMORROW, code)
