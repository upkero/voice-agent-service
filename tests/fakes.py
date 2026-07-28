"""In-memory stand-ins for everything outside this service.

FakeBookingGateway is the second implementation of the BookingGateway
port, which is what makes the port more than decoration: if the services can be
driven by an in-memory dict as easily as by HTTP, then they really do not know
which one they have.
"""

from collections.abc import Sequence
from datetime import date, time
from uuid import UUID, uuid4

from src.app.contracts.booking import BookingDTO, BookingStatus, SlotDTO
from src.app.exceptions.booking import (
    BookingNotFoundError,
    CoreUnavailableError,
    IdempotencyKeyConsumedError,
    SlotTakenError,
)
from src.app.interfaces.booking_gateway import BookingGateway
from src.app.interfaces.livekit_gateway import LiveKitGateway

TOMORROW = date(2026, 7, 24)


def make_slot(hour: int = 19, minute: int = 30, capacity: int = 6, slot_date: date = TOMORROW) -> SlotDTO:
    return SlotDTO(id=uuid4(), slot_date=slot_date, slot_time=time(hour, minute), capacity=capacity)


def seeded_slots(slot_date: date = TOMORROW) -> list[SlotDTO]:
    """The table slots ops-core-api actually seeds: 12:00, 13:30, 18:00, 19:30."""
    return [
        make_slot(12, 0, 2, slot_date),
        make_slot(13, 30, 4, slot_date),
        make_slot(18, 0, 4, slot_date),
        make_slot(19, 30, 6, slot_date),
    ]


class FakeBookingGateway(BookingGateway):
    def __init__(self, slots: Sequence[SlotDTO] | None = None) -> None:
        self.slots: dict[UUID, SlotDTO] = {slot.id: slot for slot in (slots or seeded_slots())}
        self.taken: set[UUID] = set()
        self.bookings: dict[UUID, BookingDTO] = {}
        self.keys: dict[str, UUID] = {}
        self.used_keys: list[str] = []
        self.calls: list[str] = []

    async def list_available_slots(self, slot_date: date, party_size: int) -> Sequence[SlotDTO]:
        self.calls.append("list_available_slots")
        return [
            slot
            for slot in self.slots.values()
            if slot.slot_date == slot_date and slot.id not in self.taken and slot.capacity >= party_size
        ]

    async def create_booking(
        self,
        guest_name: str,
        slot_id: UUID,
        party_size: int,
        idempotency_key: str,
    ) -> BookingDTO:
        self.calls.append("create_booking")
        self.used_keys.append(idempotency_key)

        # Mirrors ops-core-api: a known key replays its booking, unless that
        # booking was cancelled, in which case it refuses and asks for a new one.
        existing_id = self.keys.get(idempotency_key)
        if existing_id is not None:
            existing = self.bookings[existing_id]
            if existing.status is BookingStatus.CANCELLED:
                raise IdempotencyKeyConsumedError()
            return existing

        if slot_id in self.taken:
            raise SlotTakenError()

        booking = BookingDTO(
            id=uuid4(),
            guest_name=guest_name,
            slot_id=slot_id,
            party_size=party_size,
            status=BookingStatus.ACTIVE,
        )
        self.bookings[booking.id] = booking
        self.keys[idempotency_key] = booking.id
        self.taken.add(slot_id)
        return booking

    async def find_bookings(self, guest_name: str, slot_date: date) -> Sequence[BookingDTO]:
        self.calls.append("find_bookings")
        return [
            booking
            for booking in self.bookings.values()
            if booking.status is BookingStatus.ACTIVE
            and guest_name.lower() in booking.guest_name.lower()
            and self.slots[booking.slot_id].slot_date == slot_date
        ]

    async def cancel_booking(self, booking_id: UUID) -> BookingDTO:
        self.calls.append("cancel_booking")
        booking = self.bookings.get(booking_id)
        if booking is None:
            raise BookingNotFoundError()
        if booking.status is BookingStatus.CANCELLED:
            return booking
        cancelled = BookingDTO(
            id=booking.id,
            guest_name=booking.guest_name,
            slot_id=booking.slot_id,
            party_size=booking.party_size,
            status=BookingStatus.CANCELLED,
        )
        self.bookings[booking.id] = cancelled
        # Freeing the slot is the point of cancelling, and ops-core-api does it
        # in the same transaction.
        self.taken.discard(booking.slot_id)
        return cancelled


class UnavailableBookingGateway(BookingGateway):
    """Every call fails the way an unreachable ops-core-api fails."""

    async def list_available_slots(self, slot_date: date, party_size: int) -> Sequence[SlotDTO]:
        raise CoreUnavailableError()

    async def create_booking(
        self,
        guest_name: str,
        slot_id: UUID,
        party_size: int,
        idempotency_key: str,
    ) -> BookingDTO:
        raise CoreUnavailableError()

    async def find_bookings(self, guest_name: str, slot_date: date) -> Sequence[BookingDTO]:
        raise CoreUnavailableError()

    async def cancel_booking(self, booking_id: UUID) -> BookingDTO:
        raise CoreUnavailableError()


class CancelledReplayGateway(FakeBookingGateway):
    """Returns a cancelled booking from a create call.

    Represents a regressed or older ops-core-api. Nothing should ever answer a
    create this way, which is exactly why the service asserts it does not.
    """

    async def create_booking(
        self,
        guest_name: str,
        slot_id: UUID,
        party_size: int,
        idempotency_key: str,
    ) -> BookingDTO:
        return BookingDTO(
            id=uuid4(),
            guest_name=guest_name,
            slot_id=slot_id,
            party_size=party_size,
            status=BookingStatus.CANCELLED,
        )


class FakeLiveKitGateway(LiveKitGateway):
    """Readiness without a LiveKit server on the other end.

    The reason the probe became a port: before it did, /health/ready could only
    be exercised by standing one up.
    """

    def __init__(self, reachable: bool = True) -> None:
        self.reachable = reachable

    async def ping(self) -> bool:
        return self.reachable


class FakeRoom:
    """Captures what would have gone out over the data channel."""

    def __init__(self, name: str = "room-1", failing: bool = False) -> None:
        self.name = name
        self.published: list[tuple[str, str]] = []
        self.local_participant = _FakeLocalParticipant(self, failing)


class _FakeLocalParticipant:
    def __init__(self, room: "FakeRoom", failing: bool) -> None:
        self._room = room
        self._failing = failing

    async def send_text(self, text: str, *, topic: str = "") -> None:
        if self._failing:
            raise ConnectionError("data channel is down")
        self._room.published.append((topic, text))
