from typing import Any

import pytest
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions, tts
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.app.core.settings.agent import AgentSettings
from src.app.core.settings.tts import TTSSettings
from src.app.messages import greeting_line
from src.app.voice import greeting_audio

_RATE = 16000


class _Engine(tts.TTS[Any]):
    """A real livekit TTS, so the stream and its spans behave as in production."""

    def __init__(self, texts: list[str]) -> None:
        super().__init__(capabilities=tts.TTSCapabilities(streaming=False), sample_rate=_RATE, num_channels=1)
        self.texts = texts

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        self.texts.append(text)
        return _Stream(tts=self, input_text=text, conn_options=conn_options)


class _Stream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(request_id="r", sample_rate=_RATE, num_channels=1, mime_type="audio/pcm")
        output_emitter.push(bytes(2 * _RATE))
        output_emitter.flush()


@pytest.fixture(autouse=True)
def _empty_cache() -> None:
    greeting_audio._FRAMES.clear()


def test_every_language_is_synthesised_once_with_its_own_name(monkeypatch: pytest.MonkeyPatch) -> None:
    spoken: list[str] = []
    monkeypatch.setattr(greeting_audio, "create_primary_tts", lambda settings, language: _Engine(spoken))
    agent = AgentSettings(_env_file=None, language="ru", name="Мила", name_en="Mila", venue_name="Aurora")

    greeting_audio.prewarm_greetings(agent, TTSSettings(_env_file=None))

    assert sorted(spoken) == sorted([greeting_line("ru", "Мила", "Aurora"), greeting_line("en", "Mila", "Aurora")])
    frames = greeting_audio.cached_greeting("en", greeting_line("en", "Mila", "Aurora"))
    assert frames and sum(frame.duration for frame in frames) == pytest.approx(1.0, abs=0.05)


def test_the_synthesis_closes_its_stream_so_its_span_ends(
    monkeypatch: pytest.MonkeyPatch, spans: InMemorySpanExporter
) -> None:
    # An unclosed stream never ends its tts_request span: in production its child was
    # exported alone, a trace with no root.
    monkeypatch.setattr(greeting_audio, "create_primary_tts", lambda settings, language: _Engine([]))

    greeting_audio.prewarm_greetings(AgentSettings(_env_file=None), TTSSettings(_env_file=None))

    names = [span.name for span in spans.get_finished_spans()]
    assert names.count("tts_request") == 2


def test_a_failed_synthesis_leaves_that_greeting_uncached(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(settings: object, language: str) -> Any:
        raise RuntimeError("voice down")

    monkeypatch.setattr(greeting_audio, "create_primary_tts", broken)

    greeting_audio.prewarm_greetings(AgentSettings(_env_file=None), TTSSettings(_env_file=None))

    assert greeting_audio.cached_greeting("en", greeting_line("en", "Mila", "Aurora")) is None
