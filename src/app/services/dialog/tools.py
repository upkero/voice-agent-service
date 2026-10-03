"""The deterministic tool interface.

Two layers of narrowing sit between what the model says and what reaches
ops-core-api, because a language model asked for a date will occasionally
produce "next Friday" and a party size of "four":

1. An explicit JSON Schema per tool, handed to the provider verbatim. Every
   object is `additionalProperties: false` with an explicit `required` list,
   dates and times carry `pattern`, and numbers carry bounds. Schemas are
   written out rather than inferred from type hints so that what constrains the
   model is visible in one file and cannot drift when a signature changes.
2. A Pydantic model per tool, validated before dispatch. The schema is a
   request to the provider; this is the enforcement. Providers vary in how
   strictly they honour a schema, and "mostly" is not a property to build a
   booking on.

Anything that fails either layer becomes a sentence Мила can say. The dispatcher
never raises into the pipeline: an exception there is silence on a phone call,
which a guest reads as the line having dropped.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, time, timedelta
from logging import getLogger
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.app.contracts.booking import BookingSummary, SlotOffer
from src.app.core.settings.agent import AgentSettings
from src.app.exceptions.booking import BookingError
from src.app.messages import phrase
from src.app.prompts import get_prompt
from src.app.services.booking.reservation_service import ReservationService
from src.app.services.dialog.session_state import DialogSessionState

logger = getLogger(__name__)

_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
_TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
_SLOT_REF_PATTERN = r"^slot_[1-9]\d*$"
_BOOKING_REF_PATTERN = r"^booking_[1-9]\d*$"


def _text(name: str) -> str:
    """Every string below that the model reads comes from prompts/.

    A tool `description` is model-facing prose that happens to travel inside a
    JSON Schema. Leaving it inline would mean half the instructions this agent
    sends live in prompts/ and the other half in a dict literal.
    """
    return get_prompt(name).text


# --------------------------------------------------------------------------
# Argument models: the enforcement layer
# --------------------------------------------------------------------------
class _StrictArgs(BaseModel):
    # extra="forbid" mirrors additionalProperties:false. A provider that invents
    # a field is telling us it did not follow the schema, and quietly ignoring
    # that is how a party_size ends up in a field nobody reads.
    model_config = ConfigDict(extra="forbid")


class CheckAvailabilityArgs(_StrictArgs):
    booking_date: date
    party_size: int = Field(ge=1, le=100)
    preferred_time: time | None = None


class CreateBookingArgs(_StrictArgs):
    slot_ref: str = Field(pattern=_SLOT_REF_PATTERN)
    guest_name: str = Field(min_length=1, max_length=200)
    party_size: int = Field(ge=1, le=100)
    confirmed: bool


class FindBookingArgs(_StrictArgs):
    guest_name: str = Field(min_length=1, max_length=200)
    booking_date: date


class CancelBookingArgs(_StrictArgs):
    booking_ref: str = Field(pattern=_BOOKING_REF_PATTERN)
    confirmed: bool


# --------------------------------------------------------------------------
# JSON Schemas: what the provider is told
# --------------------------------------------------------------------------
CHECK_AVAILABILITY_SCHEMA: Final[dict[str, Any]] = {
    "name": "check_availability",
    "description": _text("tool_check_availability"),
    "parameters": {
        "type": "object",
        "properties": {
            "booking_date": {
                "type": "string",
                "pattern": _DATE_PATTERN,
                "description": _text("tool_check_availability_booking_date"),
            },
            "party_size": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": _text("tool_check_availability_party_size"),
            },
            "preferred_time": {
                "type": ["string", "null"],
                "pattern": _TIME_PATTERN,
                "description": _text("tool_check_availability_preferred_time"),
            },
        },
        "required": ["booking_date", "party_size", "preferred_time"],
        "additionalProperties": False,
    },
}

CREATE_BOOKING_SCHEMA: Final[dict[str, Any]] = {
    "name": "create_booking",
    "description": _text("tool_create_booking"),
    "parameters": {
        "type": "object",
        "properties": {
            "slot_ref": {
                "type": "string",
                "pattern": _SLOT_REF_PATTERN,
                "description": _text("tool_create_booking_slot_ref"),
            },
            "guest_name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
                "description": _text("tool_create_booking_guest_name"),
            },
            "party_size": {"type": "integer", "minimum": 1, "maximum": 100},
            "confirmed": {
                "type": "boolean",
                "description": _text("tool_create_booking_confirmed"),
            },
        },
        "required": ["slot_ref", "guest_name", "party_size", "confirmed"],
        "additionalProperties": False,
    },
}

FIND_BOOKING_SCHEMA: Final[dict[str, Any]] = {
    "name": "find_booking",
    "description": _text("tool_find_booking"),
    "parameters": {
        "type": "object",
        "properties": {
            "guest_name": {"type": "string", "minLength": 1, "maxLength": 200},
            "booking_date": {"type": "string", "pattern": _DATE_PATTERN},
        },
        "required": ["guest_name", "booking_date"],
        "additionalProperties": False,
    },
}

CANCEL_BOOKING_SCHEMA: Final[dict[str, Any]] = {
    "name": "cancel_booking",
    "description": _text("tool_cancel_booking"),
    "parameters": {
        "type": "object",
        "properties": {
            "booking_ref": {
                "type": "string",
                "pattern": _BOOKING_REF_PATTERN,
                "description": _text("tool_cancel_booking_booking_ref"),
            },
            "confirmed": {
                "type": "boolean",
                "description": _text("tool_cancel_booking_confirmed"),
            },
        },
        "required": ["booking_ref", "confirmed"],
        "additionalProperties": False,
    },
}

TOOL_SCHEMAS: Final[tuple[dict[str, Any], ...]] = (
    CHECK_AVAILABILITY_SCHEMA,
    CREATE_BOOKING_SCHEMA,
    FIND_BOOKING_SCHEMA,
    CANCEL_BOOKING_SCHEMA,
)


class BookingTools:
    """Dispatches validated tool calls and phrases every failure.

    Constructed per call, because it owns that call's reference table. The
    LiveKit layer wraps these methods in @function_tool and adds nothing, so
    what the tests exercise is exactly what the guest talks to.
    """

    def __init__(
        self,
        reservations: ReservationService,
        state: DialogSessionState,
        settings: AgentSettings,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._reservations = reservations
        self._state = state
        self._settings = settings
        # Injected so "is this date in the past" is testable without waiting
        # for tomorrow.
        self._today = today

    @property
    def language(self) -> str:
        return self._settings.language

    async def check_availability(self, raw: dict[str, Any]) -> dict[str, Any]:
        args = self._parse(CheckAvailabilityArgs, raw)
        if args is None:
            return self._failure("invalid_arguments")

        rejection = self._reject_unbookable(args.booking_date, args.party_size)
        if rejection is not None:
            return rejection

        async def run() -> dict[str, Any]:
            offers = await self._reservations.find_offers(
                self._state, args.booking_date, args.party_size, args.preferred_time
            )
            if not offers:
                return {"ok": True, "options": [], "say": phrase(self.language, "no_slots")}
            logger.info(
                "Offered %d slots",
                len(offers),
                extra={"room_id": self._state.room_id, "times": ReservationService.describe(offers)},
            )
            return {"ok": True, "options": [self._render_offer(offer) for offer in offers]}

        return await self._guarded(run)

    async def create_booking(self, raw: dict[str, Any]) -> dict[str, Any]:
        args = self._parse(CreateBookingArgs, raw)
        if args is None:
            return self._failure("invalid_arguments")
        if not args.confirmed:
            # The gate is not decoration. A table taken off a misheard "yes" is
            # a table the venue holds for someone who is not coming.
            return self._failure("not_confirmed")

        async def run() -> dict[str, Any]:
            summary = await self._reservations.reserve(
                self._state, args.slot_ref, args.guest_name.strip(), args.party_size
            )
            logger.info(
                "Booking created",
                extra={"room_id": self._state.room_id, "booking_ref": summary.ref},
            )
            return {"ok": True, "booking": self._render_booking(summary)}

        return await self._guarded(run)

    async def find_booking(self, raw: dict[str, Any]) -> dict[str, Any]:
        args = self._parse(FindBookingArgs, raw)
        if args is None:
            return self._failure("invalid_arguments")

        async def run() -> dict[str, Any]:
            summaries = await self._reservations.find_existing(
                self._state, args.guest_name.strip(), args.booking_date
            )
            if not summaries:
                return {"ok": True, "bookings": [], "say": phrase(self.language, "entity_not_found")}
            return {"ok": True, "bookings": [self._render_booking(item) for item in summaries]}

        return await self._guarded(run)

    async def cancel_booking(self, raw: dict[str, Any]) -> dict[str, Any]:
        args = self._parse(CancelBookingArgs, raw)
        if args is None:
            return self._failure("invalid_arguments")
        if not args.confirmed:
            return self._failure("not_confirmed")

        async def run() -> dict[str, Any]:
            summary = await self._reservations.cancel(self._state, args.booking_ref)
            logger.info(
                "Booking cancelled",
                extra={"room_id": self._state.room_id, "booking_ref": summary.ref},
            )
            return {"ok": True, "cancelled": self._render_booking(summary)}

        return await self._guarded(run)

    # ----------------------------------------------------------------------
    def _reject_unbookable(self, booking_date: date, party_size: int) -> dict[str, Any] | None:
        """Rules a voice agent should not need a network call to apply."""
        if party_size > self._settings.max_party_size:
            return self._failure("party_too_large")
        today = self._today()
        if booking_date < today:
            return self._failure("date_in_past")
        if booking_date > today + timedelta(days=self._settings.booking_horizon_days):
            return self._failure("date_too_far")
        return None

    async def _guarded(self, run: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        """Run a dispatch, converting any domain failure into something sayable.

        BookingError carries the error_code that phrases are keyed by, so a new
        failure mode gets its sentence by adding one entry, not by editing four
        except blocks.
        """
        try:
            # The gateway's retries alone can add up to half a minute against a
            # hung ops-core; on a phone call that reads as a dropped line. Past
            # the ceiling the guest hears the outage sentence instead.
            async with asyncio.timeout(self._settings.tool_wait_seconds):
                return await run()
        except TimeoutError:
            logger.warning(
                "Tool call exceeded %.1f s",
                self._settings.tool_wait_seconds,
                extra={"room_id": self._state.room_id, "error_code": "core_unavailable"},
            )
            return self._failure("core_unavailable")
        except BookingError as exc:
            logger.warning(
                "Tool call failed: %s",
                exc.error_code,
                extra={"room_id": self._state.room_id, "error_code": exc.error_code},
            )
            return self._failure(exc.error_code)
        except Exception:
            # Deliberately broad, and deliberately last. An unexpected exception
            # escaping into the pipeline is dead air, and dead air is the one
            # outcome a caller cannot interpret.
            logger.exception("Unexpected tool failure", extra={"room_id": self._state.room_id})
            return self._failure("booking_error")

    def _parse(self, model: type[BaseModel], raw: dict[str, Any]) -> Any:
        try:
            return model.model_validate(raw)
        except ValidationError as exc:
            logger.warning(
                "Rejected tool arguments for %s: %s",
                model.__name__,
                exc.error_count(),
                extra={"room_id": self._state.room_id, "errors": exc.errors(include_url=False)},
            )
            return None

    def _failure(self, code: str) -> dict[str, Any]:
        return {"ok": False, "reason": code, "say": phrase(self.language, code)}

    @staticmethod
    def _render_offer(offer: SlotOffer) -> dict[str, Any]:
        # Note what is absent: slot.id. The reference is the only handle the
        # model gets, which is what makes an invented one impossible to resolve.
        return {
            "ref": offer.ref,
            "date": offer.slot.slot_date.isoformat(),
            "time": offer.slot.slot_time.strftime("%H:%M"),
            "seats": offer.slot.capacity,
        }

    @staticmethod
    def _render_booking(summary: BookingSummary) -> dict[str, Any]:
        rendered: dict[str, Any] = {
            "ref": summary.ref,
            "guest_name": summary.guest_name,
            "party_size": summary.party_size,
        }
        if summary.slot_date is not None:
            rendered["date"] = summary.slot_date.isoformat()
        if summary.slot_time is not None:
            rendered["time"] = summary.slot_time.strftime("%H:%M")
        return rendered
