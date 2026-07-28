"""Cancelling, and what it frees.

A cancellation that leaves the table marked taken is worse than no cancellation
at all: the guest is gone and the seat stays empty all evening.
"""

from datetime import time

import pytest

from src.app.exceptions.booking import BookingNotFoundError
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


async def test_finding_a_booking_registers_it_for_cancelling(
    reservations: ReservationService, state: DialogSessionState
) -> None:
    """A reservation from an earlier call becomes cancellable the moment it is
    found, without its identifier ever being spoken."""
    offers = await reservations.find_offers(state, TOMORROW, 4, preferred_time=None)
    await reservations.reserve(state, offers[0].ref, "Petrova", 4)

    later_call = DialogSessionState("second-call")
    found = await reservations.find_existing(later_call, "Petrova", TOMORROW)

    cancelled = await reservations.cancel(later_call, found[0].ref)

    assert cancelled.guest_name == "Petrova"


async def test_a_cancelled_booking_is_not_found_again(
    reservations: ReservationService, state: DialogSessionState
) -> None:
    offers = await reservations.find_offers(state, TOMORROW, 4, preferred_time=None)
    booked = await reservations.reserve(state, offers[0].ref, "Petrova", 4)
    await reservations.cancel(state, booked.ref)

    found = await reservations.find_existing(state, "Petrova", TOMORROW)

    assert found == []
