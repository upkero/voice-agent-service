"""Saying something when the voice cannot.

The failure this guards against is silence. A caller who hears nothing cannot
tell a broken speech engine from a dropped line from an agent that is thinking,
and every second of it makes them more likely to hang up. So whenever the audio
path is unavailable, the same words go out over the room's data channel, where
any LiveKit client — the Agents Playground included — renders them as chat.

`lk.chat` is used rather than a topic of our own because it is what clients
already listen on. Inventing a private topic would mean the message is
technically published and practically invisible.
"""

from logging import getLogger
from typing import Any, Protocol

logger = getLogger(__name__)

CHAT_TOPIC = "lk.chat"


class _TextPublisher(Protocol):
    async def send_text(self, text: str, *, topic: str = ...) -> Any: ...


class ChatRoom(Protocol):
    """The two things announcing needs from a room: a name for the log line and
    somebody to publish through.

    A structural type rather than `rtc.Room` because this is the path that runs
    when everything else is broken, and it is worth being able to test without a
    live WebRTC connection. `rtc.Room` satisfies it as it stands.
    """

    @property
    def name(self) -> str: ...

    @property
    def local_participant(self) -> _TextPublisher: ...


async def announce(room: ChatRoom, text: str) -> bool:
    """Publish a line of text to everyone in the room.

    Returns whether it went out. Never raises: this is the fallback path, and a
    fallback that can throw turns one failure into two.
    """
    try:
        await room.local_participant.send_text(text, topic=CHAT_TOPIC)
        return True
    except Exception:
        logger.exception("Could not publish to the data channel", extra={"room": room.name})
        return False


class DegradationNotice:
    """Announces a given problem once per call.

    Once, because a TTS provider that is down is down for every turn, and an
    agent that repeats "my voice is unavailable" after each sentence is its own
    kind of broken. The first message is the useful one.
    """

    def __init__(self, room: ChatRoom) -> None:
        self._room = room
        self._announced: set[str] = set()

    async def announce_once(self, kind: str, text: str) -> None:
        if kind in self._announced:
            return
        self._announced.add(kind)
        logger.warning("Degraded: %s", kind, extra={"room": self._room.name, "degradation": kind})
        await announce(self._room, text)


DEGRADATION_MESSAGES: dict[str, dict[str, str]] = {
    "ru": {
        "tts": "Голос сейчас недоступен — отвечаю текстом в этом чате.",
        "stt": "Я вас не слышу — микрофон или распознавание недоступны. Напишите, пожалуйста, сообщением.",
        "startup": "Голосовой канал не поднялся. Отвечаю текстом.",
    },
    "en": {
        "tts": "My voice is unavailable right now — I'll answer here in the chat.",
        "stt": "I can't hear you — speech recognition is unavailable. Please type instead.",
        "startup": "The voice channel failed to start. I'll answer in text.",
    },
}


def degradation_message(language: str, kind: str) -> str:
    table = DEGRADATION_MESSAGES.get(language) or DEGRADATION_MESSAGES["en"]
    return table.get(kind) or DEGRADATION_MESSAGES["en"][kind]
