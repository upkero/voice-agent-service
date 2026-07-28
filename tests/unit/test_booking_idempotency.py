"""The rules that stop one guest from holding two tables.

Every case here describes something that actually happens on a phone call: a
request that times out, a model that calls a tool twice, a guest who cancels
and immediately changes their mind back.
"""

from datetime import time

import pytest

from src.app.exceptions.booking import SlotTakenError
from src.app.services.booking.ranking import NearestTimeRanking
from src.app.services.booking.reservation_service import ReservationService
from src.app.services.dialog.session_state import DialogSessionState
from tests.fakes import TOMORROW, CancelledReplayGateway, FakeBookingGateway


async def _offer(reservations: ReservationService, state: DialogSessionState, party_size: int = 4) -> str:
    offers = await reservations.find_offers(state, TOMORROW, party_size, preferred_time=time(19, 0))
    return offers[0].ref


async def test_the_model_calling_create_twice_books_one_table(reservations, state, repository) -> None:
    ref = await _offer(reservations, state)

    first = await reservations.reserve(state, ref, "Ivanov", 4)
    second = await reservations.reserve(state, ref, "Ivanov", 4)

    assert repository.used_keys[0] == repository.used_keys[1]
    assert len(repository.bookings) == 1
    # One booking, one reference. A second reference for the same table would
    # let the model believe the guest has two.
    assert first.ref == second.ref


async def test_the_key_changes_with_the_party_size(reservations, state, repository) -> None:
    """Different arguments must not collide on one key.

    The second attempt is rejected because the table is already taken — which
    is the right answer — so the assertion is on the keys that were offered,
    not on a booking that should never have happened.
    """
    ref = await _offer(reservations, state, party_size=4)
    await reservations.reserve(state, ref, "Ivanov", 4)
    with pytest.raises(SlotTakenError):
        await reservations.reserve(state, ref, "Ivanov", 5)

    assert repository.used_keys[0] != repository.used_keys[1]


async def test_the_key_changes_with_the_guest_name(reservations, state, repository) -> None:
    ref = await _offer(reservations, state)
    await reservations.reserve(state, ref, "Ivanov", 4)
    with pytest.raises(SlotTakenError):
        await reservations.reserve(state, ref, "Petrov", 4)

    assert repository.used_keys[0] != repository.used_keys[1]


async def test_two_calls_never_share_a_key(reservations, repository) -> None:
    """Different rooms are different conversations, whatever was said in them."""
    first_call = DialogSessionState("room-a")
    second_call = DialogSessionState("room-b")
    ref_a = await _offer(reservations, first_call)
    await reservations.reserve(first_call, ref_a, "Ivanov", 4)
    ref_b = await _offer(reservations, second_call)
    await reservations.reserve(second_call, ref_b, "Ivanov", 4)

    assert repository.used_keys[0] != repository.used_keys[1]


async def test_rebooking_after_a_cancellation_takes_a_real_table(reservations, state, repository) -> None:
    """The case the whole intent counter exists for.

    Same table, same name, same party size, in the same call. Without a fresh
    key ops-core-api answers 409 idempotency_key_consumed and the guest is told
    "no" about a table that is standing empty.
    """
    ref = await _offer(reservations, state)
    booked = await reservations.reserve(state, ref, "Ivanov", 4)
    first_key = repository.used_keys[-1]

    await reservations.cancel(state, booked.ref)
    again = await _offer(reservations, state)
    await reservations.reserve(state, again, "Ivanov", 4)

    assert repository.used_keys[-1] != first_key
    active = [b for b in repository.bookings.values() if b.status.value == "active"]
    assert len(active) == 1


async def test_a_consumed_key_is_recovered_exactly_once(reservations, state, repository) -> None:
    """If the counter ever misses a path, the guest still gets their table.

    The recovery is deliberately one-shot: a loop here would hammer the booking
    API on a live call.
    """
    ref = await _offer(reservations, state)
    booked = await reservations.reserve(state, ref, "Ivanov", 4)

    # Cancel through the repository only, so the session never learns about it
    # and its key stays stale — exactly the state the recovery is written for.
    await repository.cancel_booking(state.resolve_booking(booked.ref).id)
    attempts_before = repository.calls.count("create_booking")

    recovered = await reservations.reserve(state, ref, "Ivanov", 4)

    assert recovered.guest_name == "Ivanov"
    assert repository.calls.count("create_booking") == attempts_before + 2  # the refusal, then the new key


async def test_a_cancelled_booking_is_never_reported_as_a_confirmation(state) -> None:
    """Telling a guest they have a table when they do not is the worst outcome
    this service can produce, so it fails loudly instead."""
    service = ReservationService(CancelledReplayGateway(), NearestTimeRanking())
    offers = await service.find_offers(state, TOMORROW, party_size=4, preferred_time=None)

    with pytest.raises(AssertionError, match="cancelled"):
        await service.reserve(state, offers[0].ref, "Ivanov", 4)


async def test_the_key_fits_the_header_limit(reservations, state, repository) -> None:
    """ops-core-api caps Idempotency-Key at 64 characters."""
    ref = await _offer(reservations, state)
    await reservations.reserve(state, ref, "Ivanov", 4)

    assert len(repository.used_keys[0]) == 64


async def test_the_key_does_not_leak_the_guest_name(reservations, state, repository) -> None:
    """It travels in a header and lands in access logs, so it is a hash."""
    ref = await _offer(reservations, state)
    await reservations.reserve(state, ref, "Ostrovsky", 4)

    assert "Ostrovsky" not in repository.used_keys[0]


async def test_a_fake_repository_is_a_real_booking_gateway() -> None:
    """The port has two implementations, which is what makes it a port."""
    from src.app.interfaces.booking_gateway import BookingGateway

    assert isinstance(FakeBookingGateway(), BookingGateway)
