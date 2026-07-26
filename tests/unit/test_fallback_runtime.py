"""Runtime behaviour of the STT FallbackAdapter — not just that it is built.

The factory test proves a fallback *wraps* two providers. This proves the wrap
actually fails over: when the primary raises, the adapter really does hand the
request to the secondary and return its result. Two fake streaming STTs (so no
VAD is needed) stand in for "a streaming primary that is down" and "a batch
fallback that works", and the assertion is on which one's transcript comes back.

Offline: no keys, no model, no network — the point is the failover logic, which
is livekit's, exercised against this project's own client shape.
"""

import pytest
from livekit import rtc
from livekit.agents import APIConnectionError, NotGivenOr, stt, utils
from livekit.agents.language import LanguageCode
from livekit.agents.types import NOT_GIVEN, APIConnectOptions


class _FakeSTT(stt.STT):
    def __init__(self, *, text: str | None) -> None:
        # streaming=True so FallbackAdapter needs no VAD to hold it.
        super().__init__(capabilities=stt.STTCapabilities(streaming=True, interim_results=False))
        self._text = text
        self.calls = 0

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        self.calls += 1
        if self._text is None:
            raise APIConnectionError("this provider is down")
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(language=LanguageCode("en"), text=self._text)],
        )


def _frame() -> rtc.AudioFrame:
    return rtc.AudioFrame(data=b"\x00\x00" * 160, sample_rate=16000, num_channels=1, samples_per_channel=160)


async def test_a_dead_primary_fails_over_to_the_working_fallback() -> None:
    primary = _FakeSTT(text=None)  # always raises
    fallback = _FakeSTT(text="fallback worked")
    adapter = stt.FallbackAdapter([primary, fallback])

    event = await adapter.recognize([_frame()])

    assert event.alternatives[0].text == "fallback worked"
    assert primary.calls >= 1  # the primary was actually tried first
    assert fallback.calls == 1  # and the fallback actually served it


async def test_a_healthy_primary_is_used_and_the_fallback_stays_idle() -> None:
    primary = _FakeSTT(text="primary served")
    fallback = _FakeSTT(text="should not be used")
    adapter = stt.FallbackAdapter([primary, fallback])

    event = await adapter.recognize([_frame()])

    assert event.alternatives[0].text == "primary served"
    assert fallback.calls == 0  # no failover when the primary works


async def test_all_providers_down_raises_rather_than_lying() -> None:
    adapter = stt.FallbackAdapter([_FakeSTT(text=None), _FakeSTT(text=None)])

    with pytest.raises(Exception):  # noqa: B017 - livekit raises its own aggregate error type
        await adapter.recognize([_frame()])
