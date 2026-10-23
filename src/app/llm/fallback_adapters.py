"""FallbackAdapters that say who is actually answering.

livekit labels a fallback chain `livekit` / `FallbackAdapter` — in the per-turn
metrics it attaches to every message, and nowhere else is the provider named.
So with a fallback configured (the default) a latency series cannot tell Deepgram
from the offline Whisper it fails over to, which is the comparison the provider
switches are judged by. These report the first member that is not marked down.

Availability is tracked from the adapters' own public events, and members are
matched by label rather than identity: the STT adapter wraps a batch member in
a StreamAdapter, which carries the label of what it wraps.
"""

from collections.abc import Sequence
from typing import Any, Protocol

from livekit.agents import stt, tts


class _Labelled(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def provider(self) -> str: ...


class _Roster:
    def __init__(self, members: Sequence[_Labelled]) -> None:
        self._members = list(members)
        self._down: set[tuple[str, str]] = set()

    def mark(self, member: _Labelled, available: bool) -> None:
        key = (member.provider, member.model)
        if available:
            self._down.discard(key)
        else:
            self._down.add(key)

    @property
    def serving(self) -> _Labelled:
        # All down: the adapter keeps trying the first one, so report that.
        return next((m for m in self._members if (m.provider, m.model) not in self._down), self._members[0])


class ServingSTTAdapter(stt.FallbackAdapter):
    def __init__(self, instances: Sequence[stt.STT[Any]], **kwargs: Any) -> None:
        # Before super().__init__: the base class may read the labels while it builds.
        self._roster = _Roster(instances)
        super().__init__(list(instances), **kwargs)
        self.on("stt_availability_changed", lambda event: self._roster.mark(event.stt, event.available))

    @property
    def model(self) -> str:
        return self._roster.serving.model

    @property
    def provider(self) -> str:
        return self._roster.serving.provider


class ServingTTSAdapter(tts.FallbackAdapter):
    def __init__(self, instances: Sequence[tts.TTS[Any]], **kwargs: Any) -> None:
        self._roster = _Roster(instances)
        super().__init__(list(instances), **kwargs)
        self.on("tts_availability_changed", lambda event: self._roster.mark(event.tts, event.available))

    @property
    def model(self) -> str:
        return self._roster.serving.model

    @property
    def provider(self) -> str:
        return self._roster.serving.provider
