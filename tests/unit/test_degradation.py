"""What the guest gets when the voice does not work.

Anything except silence. A caller who hears nothing cannot tell a broken speech
engine from a dropped line, and hangs up either way.
"""

from src.app.services.dialog.session_state import DialogSessionState
from src.app.voice.confirmation import ConfirmationTracker
from src.app.voice.degradation import CHAT_TOPIC, DegradationNotice, announce, degradation_message
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
    from src.app.voice.degradation import DEGRADATION_MESSAGES

    assert set(DEGRADATION_MESSAGES["ru"]) == set(DEGRADATION_MESSAGES["en"])
    for kind in DEGRADATION_MESSAGES["en"]:
        assert degradation_message("ru", kind).strip()
        assert degradation_message("en", kind).strip()


def test_an_unknown_language_falls_back_rather_than_going_quiet() -> None:
    assert degradation_message("de", "tts") == degradation_message("en", "tts")


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
    state._bookings["booking_1"] = object()  # noqa: SLF001 - simulating a taken table
    tracker = ConfirmationTracker(state)

    tracker.on_conversation_item(_Event(_Item("assistant", interrupted=True)))

    assert tracker.confirmation_spoken is False


def test_a_completed_reply_after_a_booking_counts() -> None:
    state = DialogSessionState("room-1")
    state._bookings["booking_1"] = object()  # noqa: SLF001
    tracker = ConfirmationTracker(state)

    tracker.on_conversation_item(_Event(_Item("assistant")))

    assert tracker.confirmation_spoken is True


def test_a_user_turn_is_not_a_confirmation() -> None:
    state = DialogSessionState("room-1")
    state._bookings["booking_1"] = object()  # noqa: SLF001
    tracker = ConfirmationTracker(state)

    tracker.on_conversation_item(_Event(_Item("user")))

    assert tracker.confirmation_spoken is False
