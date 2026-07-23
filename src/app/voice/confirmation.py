"""Did the guest actually hear that the table was booked.

This exists so one log field can be true rather than plausible. A booking that
exists while the caller believes it does not is the only orphan the idempotency
key cannot prevent — the call drops between ops-core-api answering and Мила
finishing the sentence — and the whole response to it is "make it visible".

A field called `confirmation_spoken` that is really "a booking was made and the
process is exiting" would be worse than no field: someone would trust it. So it
tracks the thing it claims to: an assistant turn that reached the guest after
the booking was taken.
"""

from logging import getLogger
from typing import Any

from src.app.services.dialog.session_state import DialogSessionState

logger = getLogger(__name__)


class ConfirmationTracker:
    def __init__(self, state: DialogSessionState) -> None:
        self._state = state
        self._spoken_after_booking = False

    def on_conversation_item(self, event: Any) -> None:
        """Note assistant turns that follow a booking.

        Interrupted turns do not count. If the guest cut Мила off mid-sentence,
        there is no reason to believe the confirmation landed.
        """
        item = getattr(event, "item", None)
        if getattr(item, "role", None) != "assistant":
            return
        if getattr(item, "interrupted", False):
            return
        if self._state.booking_refs():
            self._spoken_after_booking = True

    @property
    def confirmation_spoken(self) -> bool:
        return self._spoken_after_booking

    def report(self, room_name: str, reason: str = "") -> None:
        refs = self._state.booking_refs()
        if not refs:
            return
        if self._spoken_after_booking:
            logger.info(
                "Call ended with a confirmed booking",
                extra={"room": room_name, "booking_refs": refs, "confirmation_spoken": True},
            )
            return
        # Deliberately not auto-cancelled. Releasing a table the guest agreed to
        # because one sentence went missing is the worse failure; a human can
        # settle this from the log with DELETE /api/v1/bookings/{id}.
        logger.warning(
            "Call ended before the booking was confirmed out loud",
            extra={
                "room": room_name,
                "booking_refs": refs,
                "confirmation_spoken": False,
                "shutdown_reason": reason,
            },
        )
