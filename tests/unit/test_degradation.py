"""What the guest gets when the voice does not work.

Anything except silence. A caller who hears nothing cannot tell a broken speech
engine from a dropped line, and hangs up either way.
"""

import asyncio
import time
from collections.abc import Callable
from typing import Any, cast
from uuid import uuid4

from livekit.agents import AgentSession, ErrorEvent, stt, tts

from src.app.contracts.booking import BookingDTO, BookingStatus
from src.app.messages import degradation_message
from src.app.services.dialog.session_state import DialogSessionState
from src.app.voice.confirmation import ConfirmationTracker
from src.app.voice.degradation import CHAT_TOPIC, DegradationNotice, announce
from src.app.voice.session import register_degradation_notices
from tests.fakes import FakeRoom


async def test_a_notice_goes_out_on_the_chat_topic() -> None:
    """The topic clients already listen on. A private one would publish the
    message and hide it at the same time."""
    room = FakeRoom()

    assert await announce(room, "hello") is True
    assert room.published == [(CHAT_TOPIC, "hello")]


async def test_a_failing_data_channel_does_not_raise() -> None:
    """This is the fallback path; a fallback that throws makes one failure two."""
    room = FakeRoom(failing=True)

    assert await announce(room, "hello") is False


async def test_a_problem_is_announced_once_not_every_turn() -> None:
    room = FakeRoom()
    notice = DegradationNotice(room)

    await notice.announce_once("tts", degradation_message("en", "tts"))
    await notice.announce_once("tts", degradation_message("en", "tts"))

    assert len(room.published) == 1


async def test_different_problems_are_announced_separately() -> None:
    room = FakeRoom()
    notice = DegradationNotice(room)

    await notice.announce_once("tts", degradation_message("en", "tts"))
    await notice.announce_once("stt", degradation_message("en", "stt"))

    assert len(room.published) == 2


def test_every_degradation_message_exists_in_both_languages() -> None:
    from src.app.messages import DEGRADATION_MESSAGES

    assert set(DEGRADATION_MESSAGES["ru"]) == set(DEGRADATION_MESSAGES["en"])
    for kind in DEGRADATION_MESSAGES["en"]:
        assert degradation_message("ru", kind).strip()
        assert degradation_message("en", kind).strip()


def test_an_unknown_language_falls_back_rather_than_going_quiet() -> None:
    assert degradation_message("de", "tts") == degradation_message("en", "tts")


def _booking() -> BookingDTO:
    """A table taken in this call — the tracker only cares that one exists."""
    return BookingDTO(id=uuid4(), guest_name="Anna", slot_id=uuid4(), party_size=2, status=BookingStatus.ACTIVE)


class _Item:
    def __init__(self, role: str, interrupted: bool = False) -> None:
        self.role = role
        self.interrupted = interrupted


class _Event:
    def __init__(self, item: _Item) -> None:
        self.item = item


def test_confirmation_is_not_claimed_without_a_booking() -> None:
    state = DialogSessionState("room-1")
    tracker = ConfirmationTracker(state)

    tracker.on_conversation_item(_Event(_Item("assistant")))

    assert tracker.confirmation_spoken is False


def test_an_interrupted_reply_does_not_count_as_confirmation() -> None:
    """If the guest talked over it, there is no reason to think it landed."""
    state = DialogSessionState("room-1")
    state._bookings["booking_1"] = _booking()  # noqa: SLF001 - simulating a taken table
    tracker = ConfirmationTracker(state)

    tracker.on_conversation_item(_Event(_Item("assistant", interrupted=True)))

    assert tracker.confirmation_spoken is False


def test_a_completed_reply_after_a_booking_counts() -> None:
    state = DialogSessionState("room-1")
    state._bookings["booking_1"] = _booking()  # noqa: SLF001
    tracker = ConfirmationTracker(state)

    tracker.on_conversation_item(_Event(_Item("assistant")))

    assert tracker.confirmation_spoken is True


def test_a_user_turn_is_not_a_confirmation() -> None:
    state = DialogSessionState("room-1")
    state._bookings["booking_1"] = _booking()  # noqa: SLF001
    tracker = ConfirmationTracker(state)

    tracker.on_conversation_item(_Event(_Item("user")))

    assert tracker.confirmation_spoken is False


# --- mid-conversation audio failures ------------------------------------------
class _FakeSession:
    """Captures the listener instead of running a real AgentSession."""

    def __init__(self) -> None:
        self.handler: Callable[[ErrorEvent], None] | None = None

    def on(self, event: str, callback: Callable[[ErrorEvent], None]) -> None:
        assert event == "error"
        self.handler = callback


def _error_event(error_type: type[stt.STTError] | type[tts.TTSError], *, recoverable: bool) -> ErrorEvent:
    error = error_type(
        type="stt_error" if error_type is stt.STTError else "tts_error",
        timestamp=time.time(),
        label="provider",
        error=Exception("provider is down"),
        recoverable=recoverable,
    )
    return ErrorEvent(type="error", error=error, source=None)


async def _fire(recoverable: bool, error_type: type[stt.STTError] | type[tts.TTSError] = stt.STTError) -> FakeRoom:
    room = FakeRoom()
    session = _FakeSession()
    register_degradation_notices(cast(AgentSession[Any], session), DegradationNotice(room), "en")

    assert session.handler is not None
    session.handler(_error_event(error_type, recoverable=recoverable))
    # The listener is synchronous and schedules the announcement; yield to it.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    return room


async def test_an_exhausted_stt_chain_is_announced() -> None:
    """Every configured provider has already been tried by the time this fires."""
    room = await _fire(recoverable=False)

    assert room.published == [(CHAT_TOPIC, degradation_message("en", "stt"))]


async def test_a_recoverable_blip_says_nothing() -> None:
    """livekit will retry it. Announcing "I can't hear you" on the first hiccup
    is how a working call gets talked out of being one."""
    room = await _fire(recoverable=True)

    assert room.published == []


async def test_a_dead_voice_is_announced_mid_conversation_too() -> None:
    """Before this handler, only a greeting that failed to speak was announced."""
    room = await _fire(recoverable=False, error_type=tts.TTSError)

    assert room.published == [(CHAT_TOPIC, degradation_message("en", "tts"))]
