"""Internal DTOs for the booking domain.

Frozen dataclasses rather than Pydantic models: these cross layer boundaries on
every turn of a conversation and carry no validation duty, which belongs at the
edges — the tool schemas above them and ops-core-api below them.
"""

from dataclasses import dataclass
from datetime import date, time
from enum import StrEnum
from uuid import UUID


class BookingStatus(StrEnum):
    """Mirrors ops-core-api's BookingStatus.

    Duplicated deliberately: importing an enum across service boundaries would
    couple two deployables that are meant to version independently. The adapter
    is the one place that translates, and an unknown value fails there.
    """

    ACTIVE = "active"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class SlotDTO:
    id: UUID
    slot_date: date
    slot_time: time
    capacity: int


@dataclass(frozen=True, slots=True)
class BookingDTO:
    id: UUID
    guest_name: str
    slot_id: UUID
    party_size: int
    status: BookingStatus


@dataclass(frozen=True, slots=True)
class SlotOffer:
    """A slot as the model sees it: a short ref and speakable details.

    `ref` is what travels through the LLM; `slot.id` never does. See
    services/dialog/session_state.py for why.
    """

    ref: str
    slot: SlotDTO


@dataclass(frozen=True, slots=True)
class BookingSummary:
    """A booking as the model sees it. No UUID, by construction."""

    ref: str
    guest_name: str
    party_size: int
    slot_date: date | None
    slot_time: time | None
    # Spoken once, when the booking is made: what proves ownership on a later call.
    code: str | None = None
