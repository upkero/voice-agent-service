from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date
from uuid import UUID

from src.app.contracts.booking import BookingDTO, SlotDTO


class BookingGateway(ABC):
    """The port through which this service reaches booking data.

    Deliberately identical in shape to a direct database repository even though
    the only implementation talks HTTP to ops-core-api. That is the point of the
    Adapter sitting behind it: ReservationService cannot tell whether a slot came
    from a SQL row or a JSON response, so the transport can change without the
    business rules noticing — and the test fakes are the second implementation
    that proves it.
    """

    @abstractmethod
    async def list_available_slots(self, slot_date: date, party_size: int) -> Sequence[SlotDTO]:
        """Slots free on that date with room for the party.

        Party size belongs in the port rather than in the service because it is
        a data question, not a business rule: a database-backed implementation
        would answer it with a WHERE clause. That the current implementation has
        to filter after fetching — ops-core-api offers no capacity query — is a
        detail of that implementation, which is precisely what a port hides.
        """

    @abstractmethod
    async def create_booking(
        self,
        guest_name: str,
        slot_id: UUID,
        party_size: int,
        idempotency_key: str,
    ) -> BookingDTO:
        """Take the table.

        `idempotency_key` is required, not optional: every caller in this
        service has one, and making it optional would invite the one code path
        that forgets it and double-books on a retry.
        """

    @abstractmethod
    async def find_bookings(self, guest_name: str, slot_date: date) -> Sequence[BookingDTO]:
        """Active bookings under that name on that date."""

    @abstractmethod
    async def cancel_booking(self, booking_id: UUID) -> BookingDTO:
        """Cancel and free the slot.

        Idempotent on the server: cancelling twice returns the same booking and
        the same success, which is exactly what a dropped call needs.
        """
