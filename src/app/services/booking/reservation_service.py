"""Reservation rules, independent of how the guest reached them.

Nothing in this module imports LiveKit or FastAPI. It is driven identically by
a voice call, an HTTP request or a test, which is what makes it the layer worth
covering with tests rather than the pipeline wiring around it.
"""

import hmac
from collections.abc import Sequence
from datetime import date, time
from hashlib import sha256
from logging import getLogger
from uuid import UUID

from src.app.contracts.booking import BookingStatus, BookingSummary, SlotDTO, SlotOffer
from src.app.exceptions.booking import (
    IdempotencyKeyConsumedError,
    InactiveBookingError,
    SlotCapacityError,
    WrongBookingCodeError,
)
from src.app.interfaces.booking_gateway import BookingGateway
from src.app.services.booking.name_match import names_match
from src.app.services.booking.ranking import rank_slots
from src.app.services.dialog.session_state import DialogSessionState

logger = getLogger(__name__)

# Three is what a person can hold in their head when it is read to them out
# loud. Offering the whole diary is how a guest ends up asking "sorry, what
# was the second one again?".
MAX_OFFERS = 3


class ReservationService:
    def __init__(self, bookings: BookingGateway, code_key: bytes) -> None:
        self._bookings = bookings
        self._code_key = code_key

    async def find_offers(
        self,
        state: DialogSessionState,
        booking_date: date,
        party_size: int,
        preferred_time: time | None,
    ) -> list[SlotOffer]:
        """Free tables for that date and party, best first.

        Returning the nearest alternatives rather than a yes/no is the whole
        job: the diary rarely holds the exact time a guest names, and an agent
        that answers "no" to 19:00 when 19:30 is free is worse than useless.
        """
        slots = await self._bookings.list_available_slots(booking_date, party_size)
        # Several tables can be free at one time, and the guest picks a time,
        # not a table: keep the smallest table that fits at each time, so the
        # three offers are three times and a couple doesn't take the eight-seater.
        smallest: dict[time, SlotDTO] = {}
        for slot in sorted(slots, key=lambda slot: slot.capacity):
            smallest.setdefault(slot.slot_time, slot)
        ranked = rank_slots(list(smallest.values()), preferred_time)
        if preferred_time is None and len(ranked) > MAX_OFFERS:
            # No time named: the first three would always be lunch. Spread the
            # offers over the day instead (earliest, middle, latest), so a guest
            # who wanted the evening hears an evening time.
            last = len(ranked) - 1
            ranked = [ranked[round(i * last / (MAX_OFFERS - 1))] for i in range(MAX_OFFERS)]
        return state.offer_slots(ranked[:MAX_OFFERS])

    async def reserve(
        self,
        state: DialogSessionState,
        slot_ref: str,
        guest_name: str,
        party_size: int,
    ) -> BookingSummary:
        """Take a table that was offered in this conversation.

        Resolving the reference first means an identifier the session never
        issued is rejected here, before anything is sent to ops-core-api.
        """
        slot = state.resolve_slot(slot_ref)
        if party_size > slot.capacity:
            # The offer was made for a smaller party (the guest changed the
            # number, or it was checked before they gave it). The guest chose a
            # time, not a table: take the smallest free one at that time that fits.
            slot = await self._table_that_fits(slot, party_size)
        key = self._idempotency_key(state, slot_ref, guest_name, party_size)

        try:
            booking = await self._bookings.create_booking(guest_name, slot.id, party_size, key)
        except IdempotencyKeyConsumedError:
            # The key belonged to a booking that has since been cancelled, so
            # ops-core-api refuses to replay it and tells us to use a new one.
            # Reaching this means intent_seq missed a path: the table is
            # genuinely free and the remedy is known, so failing the guest here
            # would be a choice rather than a limitation.
            #
            # Deliberately outside the transport retry policy. That one repeats
            # calls that might succeed unchanged; this one changes the request
            # because the server told us to, and it happens exactly once.
            logger.warning(
                "Idempotency key was consumed; opening a new intent and retrying once.",
                extra={"room_id": state.room_id, "slot_ref": slot_ref},
            )
            state.close_intent()
            retry_key = self._idempotency_key(state, slot_ref, guest_name, party_size)
            booking = await self._bookings.create_booking(guest_name, slot.id, party_size, retry_key)

        if booking.status is not BookingStatus.ACTIVE:
            # A confirmation is the one thing this agent must never say wrongly:
            # a guest told "you have a table" stops looking. If what came back
            # is not an active booking, that is a bug worth an error, not a
            # sentence spoken out loud.
            raise InactiveBookingError(
                f"ops-core-api returned a {booking.status.value} booking for a create call: {booking.id}"
            )

        return state.register_booking(
            booking, slot_date=slot.slot_date, slot_time=slot.slot_time, code=self.booking_code(booking.id)
        )

    async def _table_that_fits(self, offered: SlotDTO, party_size: int) -> SlotDTO:
        free = await self._bookings.list_available_slots(offered.slot_date, party_size)
        same_time = [slot for slot in free if slot.slot_time == offered.slot_time]
        if not same_time:
            raise SlotCapacityError()
        return min(same_time, key=lambda slot: slot.capacity)

    async def find_existing(
        self,
        state: DialogSessionState,
        guest_name: str,
        booking_date: date,
        code: str | None = None,
    ) -> list[BookingSummary]:
        """Active bookings under a name on a date, as speakable summaries.

        Without a code they can only be read back. With the right one, the
        matching booking becomes cancellable; every code check spends one of
        this call's few attempts.

        A name is required by the caller's schema, not defaulted here: this
        endpoint reveals who is dining where, and an agent that will list the
        evening's guests to anyone who asks is a data leak wearing a feature's
        clothes.

        The name is matched here, not by ops-core-api: both the stored name and
        this one were heard by speech recognition on different calls, and a
        substring match fails on a Russian case ending. That means the whole
        day's bookings are fetched, which is why only the ones that match are
        ever returned, registered in the session or logged.
        """
        if code is not None:
            state.spend_code_attempt()
        on_the_day = await self._bookings.list_active_bookings(booking_date)
        bookings = [b for b in on_the_day if names_match(guest_name, b.guest_name)]
        if code is not None and bookings:
            # compare_digest: a code check should not leak through its timing.
            bookings = [b for b in bookings if hmac.compare_digest(self.booking_code(b.id), code)]
            if not bookings:
                raise WrongBookingCodeError()
        # The date is known only because the guest just said it: ops-core-api
        # can filter bookings by date but does not return the slot's date or
        # time, so that is the most we can read back. See the README.
        return [
            state.register_booking(booking, slot_date=booking_date, cancellable=code is not None)
            for booking in bookings
        ]

    def booking_code(self, booking_id: UUID) -> str:
        """Four digits a guest can note down and say back on a later call.

        Derived, not stored: ops-core-api has no field for it, and an HMAC of
        the booking id needs none. Booking ids are visible elsewhere (ops-core's
        own API), so a plain hash would let anyone compute the code; the key is
        the ops-core key, held only by those who could cancel through ops-core
        directly anyway. Rotating that key changes every code.
        """
        digest = hmac.new(self._code_key, booking_id.bytes, sha256).digest()
        return f"{int.from_bytes(digest[:8], 'big') % 10_000:04d}"

    async def cancel(self, state: DialogSessionState, booking_ref: str) -> BookingSummary:
        """Cancel a booking made during this conversation, and only such a booking."""
        booking = state.resolve_cancellable(booking_ref)
        summary = state.summarise_booking(booking_ref)
        await self._bookings.cancel_booking(booking.id)
        # Closing the intent is what lets the guest immediately rebook the same
        # table under the same name for the same party without the retry token
        # from the cancelled booking being refused.
        state.close_intent()
        return summary

    @staticmethod
    def _idempotency_key(
        state: DialogSessionState,
        slot_ref: str,
        guest_name: str,
        party_size: int,
    ) -> str:
        """One key per reservation intent, not per HTTP call.

        A transport retry and a second identical tool call from the model both
        hash to this same value, so ops-core-api replays the booking it already
        made instead of taking a second table. `intent_seq` is what stops the
        key from outliving the intent: after a cancellation the same arguments
        must mean a new reservation, not a consumed token.

        sha256 hex is 64 characters, exactly the maximum ops-core-api accepts
        for the header — and hashing keeps the guest's name out of a value that
        travels in a header and lands in access logs.
        """
        slot = state.resolve_slot(slot_ref)
        material = f"{state.room_id}:{state.intent_seq}:{slot.id}:{guest_name}:{party_size}"
        return sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def describe(offers: Sequence[SlotOffer]) -> str:
        """Times only, for logging. Never the identifiers."""
        return ", ".join(offer.slot.slot_time.strftime("%H:%M") for offer in offers)
