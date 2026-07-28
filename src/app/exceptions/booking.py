"""Typed failures from the booking domain.

ops-core-api answers every failure with a uniform {detail, error_code} envelope,
and the adapter switches on `error_code` rather than the status code. Three
different things arrive as 409 — the slot went to someone else, the party does
not fit, the retry token was consumed — and each needs a different sentence out
of Мила's mouth. Collapsing them into "409" would produce one useless apology
for three fixable situations.
"""

from src.app.exceptions.base import BaseAppException


class BookingError(BaseAppException):
    """Base for anything that goes wrong reaching or using the booking API."""

    error_code = "booking_error"
    default_detail = "Booking operation failed."


class CoreUnavailableError(BookingError):
    """The booking API could not be reached, or kept failing.

    This is the one the guest hears about most gently: it is not their fault
    and there is nothing they can do, so Мила says the diary is unreachable
    rather than reciting a status code.
    """

    status_code = 503
    error_code = "core_unavailable"
    default_detail = "The booking service is unavailable."


class CoreRateLimitedError(BookingError):
    """ops-core-api is throttling us, and still was after the last attempt.

    Kept apart from CoreUnavailableError on purpose: "wait and try again" is a
    different fact from "it is broken", and it is the only one that comes with a
    number attached. The `Retry-After` the upstream sent travels on `headers`.
    """

    status_code = 429
    error_code = "core_rate_limited"
    default_detail = "The booking service is rate limiting us. Please retry shortly."


class SlotTakenError(BookingError):
    """Someone else booked the slot between the offer and the confirmation.

    Twenty seconds pass while a guest thinks it over, and the last 19:30 table
    can go in that window. Reachable in normal operation, not an edge case.
    """

    status_code = 409
    error_code = "slot_unavailable"
    default_detail = "That table has just been taken."


class SlotCapacityError(BookingError):
    """The party does not fit the slot."""

    status_code = 409
    error_code = "slot_capacity_exceeded"
    default_detail = "That table seats fewer people than the party."


class IdempotencyKeyConsumedError(BookingError):
    """The retry token was already used by a booking that has since been cancelled.

    Recovered internally by minting a fresh key — see ReservationService.reserve.
    The guest never hears about this one.
    """

    status_code = 409
    error_code = "idempotency_key_consumed"
    default_detail = "The booking made with this key was cancelled."


class IdempotencyKeyReusedError(BookingError):
    """The retry token was replayed with different arguments.

    Impossible by construction here, because the arguments are the key's input.
    If it ever surfaces it is a bug in this service, so it is raised and logged
    rather than smoothed over.
    """

    status_code = 409
    error_code = "idempotency_key_reused"
    default_detail = "This retry token was already used for a different booking."


class BookingNotFoundError(BookingError):
    status_code = 404
    error_code = "entity_not_found"
    default_detail = "That reservation was not found."


class UnknownReferenceError(BookingError):
    """The model used a slot or booking reference this session never issued.

    Raised before any HTTP call: an invented reference is caught here rather
    than being sent to ops-core-api to be resolved into somebody else's table.
    """

    status_code = 422
    error_code = "unknown_reference"
    default_detail = "That reference is not one I offered."
