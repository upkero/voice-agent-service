from abc import ABC

from livekit.agents import stt


class STTClient(stt.STT, ABC):
    """The project's speech-to-text port.

    It subclasses livekit's `stt.STT` instead of declaring a parallel ABC with a
    bridge between the two. A second hierarchy would need an adapter in both
    directions and would buy nothing: the pipeline can only consume `stt.STT`,
    so any port that is not one has to be converted back into one before use.

    Naming it here is still worth the three lines. The factory and every type
    hint in this codebase reference `STTClient`, so the dependency on a
    third-party base class is stated in exactly one file, and the concrete
    clients inherit from something this project owns.
    """
