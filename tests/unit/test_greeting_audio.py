from typing import Any

import pytest

from src.app.core.settings.agent import AgentSettings
from src.app.core.settings.tts import TTSSettings
from src.app.messages import greeting_line
from src.app.voice import greeting_audio


class _Event:
    def __init__(self, frame: object) -> None:
        self.frame = frame


class _Engine:
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts

    async def synthesize(self, text: str) -> Any:
        self._texts.append(text)
        for part in ("a", "b"):
            yield _Event(f"{text[:5]}:{part}")

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _empty_cache() -> None:
    greeting_audio._FRAMES.clear()


def test_every_language_is_synthesised_once_with_its_own_name(monkeypatch: pytest.MonkeyPatch) -> None:
    spoken: list[str] = []
    monkeypatch.setattr(greeting_audio, "create_primary_tts", lambda settings, language: _Engine(spoken))
    agent = AgentSettings(_env_file=None, language="ru", name="Мила", name_en="Mila", venue_name="Aurora")

    greeting_audio.prewarm_greetings(agent, TTSSettings(_env_file=None))

    assert sorted(spoken) == sorted(
        [greeting_line("ru", "Мила", "Aurora"), greeting_line("en", "Mila", "Aurora")]
    )
    assert greeting_audio.cached_greeting("en", greeting_line("en", "Mila", "Aurora")) == ["Hello:a", "Hello:b"]


def test_a_failed_synthesis_leaves_that_greeting_uncached(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(settings: object, language: str) -> Any:
        raise RuntimeError("voice down")

    monkeypatch.setattr(greeting_audio, "create_primary_tts", broken)

    greeting_audio.prewarm_greetings(AgentSettings(_env_file=None), TTSSettings(_env_file=None))

    assert greeting_audio.cached_greeting("en", greeting_line("en", "Mila", "Aurora")) is None
