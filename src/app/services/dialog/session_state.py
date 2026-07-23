"""Per-call memory: short references standing in for identifiers.

A UUID is unspeakable. Thirty-six characters that a guest cannot hear, repeat
or verify, and that a language model can silently mistype one character of on
its way back into a tool call. So no identifier ever enters the model's
context: the model is given `slot_2`, and this object holds what `slot_2` means.

Two properties fall out of that, and both matter more than the tidiness:

* A reference this session never issued cannot be resolved, so a hallucinated
  identifier fails here — before any HTTP call — instead of being sent to
  ops-core-api to be turned into somebody else's table.
* References are short and predictable, so the tool schema can describe the
  shape (`^slot_[1-9]\\d*$`) and the model is told what a valid one looks like.

The schema constrains the *shape* and this object owns the *membership*. Baking
the live references into the schema as an `enum` was the alternative; it would
have meant rebuilding and re-registering the tool definitions after every
availability lookup, to tighten a check that already happens here with
certainty. The static pattern rejects nothing this would have caught.
"""

from datetime import date, time

from src.app.contracts.booking import BookingDTO, BookingSummary, SlotDTO, SlotOffer
from src.app.exceptions.booking import UnknownReferenceError


class DialogSessionState:
    """Scoped to one room. Not shared, not persisted, gone when the call ends."""

    def __init__(self, room_id: str) -> None:
        self._room_id = room_id
        self._slots: dict[str, SlotDTO] = {}
        self._bookings: dict[str, BookingDTO] = {}
        self._summaries: dict[str, BookingSummary] = {}
        # Incremented on every cancellation. It is what makes the idempotency
        # key describe the current *intent* rather than a repeatable set of
        # arguments — see ReservationService.reserve for why that is required.
        self._intent_seq = 0

    @property
    def room_id(self) -> str:
        return self._room_id

    @property
    def intent_seq(self) -> int:
        return self._intent_seq

    def offer_slots(self, slots: list[SlotDTO]) -> list[SlotOffer]:
        """Issue fresh references for a set of slots.

        Previous slot references are dropped: they described an availability
        answer that is now stale, and keeping them alive would let the model
        accept an offer it made two questions ago.
        """
        self._slots.clear()
        offers = [SlotOffer(ref=f"slot_{index}", slot=slot) for index, slot in enumerate(slots, start=1)]
        self._slots = {offer.ref: offer.slot for offer in offers}
        return offers

    def resolve_slot(self, ref: str) -> SlotDTO:
        slot = self._slots.get(ref)
        if slot is None:
            raise UnknownReferenceError(f"Slot reference '{ref}' was not offered in this conversation.")
        return slot

    def slot_refs(self) -> list[str]:
        return list(self._slots)

    def register_booking(
        self,
        booking: BookingDTO,
        *,
        slot_date: date | None = None,
        slot_time: time | None = None,
    ) -> BookingSummary:
        """Remember a booking so it can be cancelled later in the same call.

        This is what lets a guest who changes their mind be handled without a
        lookup: the reference is already here, so cancelling goes straight to
        the identifier with no name matching and no chance of reaching somebody
        else's table.
        """
        # A replayed booking is the same booking. Issuing it a second reference
        # would show the model two reservations where the guest has one, and
        # invite it to offer to cancel a table that does not separately exist.
        for existing_ref, existing in self._bookings.items():
            if existing.id == booking.id:
                return self._summaries[existing_ref]

        ref = f"booking_{len(self._bookings) + 1}"
        summary = BookingSummary(
            ref=ref,
            guest_name=booking.guest_name,
            party_size=booking.party_size,
            slot_date=slot_date,
            slot_time=slot_time,
        )
        self._bookings[ref] = booking
        self._summaries[ref] = summary
        return summary

    def resolve_booking(self, ref: str) -> BookingDTO:
        booking = self._bookings.get(ref)
        if booking is None:
            raise UnknownReferenceError(f"Booking reference '{ref}' is not one from this conversation.")
        return booking

    def summarise_booking(self, ref: str) -> BookingSummary:
        summary = self._summaries.get(ref)
        if summary is None:
            raise UnknownReferenceError(f"Booking reference '{ref}' is not one from this conversation.")
        return summary

    def booking_refs(self) -> list[str]:
        return list(self._bookings)

    def close_intent(self) -> None:
        """Mark the current reservation intent as finished.

        Called after a cancellation. The next booking attempt then hashes to a
        new idempotency key, so ops-core-api treats it as a new reservation
        instead of refusing it as a consumed retry token.
        """
        self._intent_seq += 1
