from abc import ABC
from typing import Any

from livekit.agents import tts


class TTSClient(tts.TTS[Any], ABC):
    """The project's text-to-speech port.

    Same reasoning as STTClient: one owned name over livekit's contract, no
    parallel hierarchy and no bridge. Kept separate from STTClient rather than
    merged into an "audio client" because the two are configured, swapped and
    fail independently — that separation is the whole point of having two
    provider settings blocks instead of one.
    """
