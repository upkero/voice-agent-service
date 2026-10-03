"""Reservation rules, independent of how the guest reached them.

Nothing in this module imports LiveKit or FastAPI. It is driven identically by
a voice call, an HTTP request or a test, which is what makes it the layer worth
covering with tests rather than the pipeline wiring around it.
"""

from collections.abc import Sequence
from datetime import date, time
from hashlib import sha256
from logging import getLogger

from src.app.contracts.booking import BookingStatus, BookingSummary, SlotOffer
from src.app.exceptions.booking import IdempotencyKeyConsumedError, InactiveBookingError
from src.app.interfaces.booking.slot_ranking_strategy import SlotRankingStrategy
from src.app.interfaces.booking_gateway import BookingGateway
from src.app.services.dialog.session_state import DialogSessionState

logger = getLogger(__name__)

# Three is what a person can hold in their head when it is read to them out
# loud. Offering the whole diary is how a guest ends up asking "sorry, what
# was the second one again?".
MAX_OFFERS = 3


class ReservationService:
    def __init__(self, bookings: BookingGateway, ranking: SlotRankingStrategy) -> None:
        self._bookings = bookings
        self._ranking = ranking

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
        ranked = self._ranking.rank(slots, preferred_time)
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

        return state.register_booking(booking, slot_date=slot.slot_date, slot_time=slot.slot_time)

    async def find_existing(
        self,
        state: DialogSessionState,
        guest_name: str,
        booking_date: date,
    ) -> list[BookingSummary]:
        """Active bookings under a name on a date, as speakable summaries.

        A name is required by the caller's schema, not defaulted here: this
        endpoint reveals who is dining where, and an agent that will list the
        evening's guests to anyone who asks is a data leak wearing a feature's
        clothes.
        """
        bookings = await self._bookings.find_bookings(guest_name, booking_date)
        # The date is known only because the guest just said it: ops-core-api
        # can filter bookings by date but does not return the slot's date or
        # time, so that is the most we can read back. See the README.
        return [state.register_booking(booking, slot_date=booking_date, made_here=False) for booking in bookings]

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
