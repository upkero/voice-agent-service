import logging
from collections.abc import AsyncIterator

import pytest
from livekit.agents import Agent, ModelSettings, llm

from src.app.core.settings.agent import AgentSettings
from src.app.prompts import get_prompt
from src.app.services.dialog.flow import DialogFlow
from src.app.services.dialog.tools import BookingTools
from src.app.voice.agent import BookingAgent, drop_greeting, without_leading_greeting


def _chunk(text: str) -> llm.ChatChunk:
    return llm.ChatChunk(id="c", delta=llm.ChoiceDelta(role="assistant", content=text))


async def _stream(*parts: str) -> AsyncIterator[llm.ChatChunk]:
    for part in parts:
        yield _chunk(part)


async def _text(*parts: str) -> str:
    out = ""
    async for chunk in without_leading_greeting(_stream(*parts)):
        assert isinstance(chunk, llm.ChatChunk) and chunk.delta is not None
        out += chunk.delta.content or ""
    return out


def test_drop_greeting() -> None:
    assert drop_greeting("Здравствуйте! На какую дату?") == "На какую дату?"
    assert drop_greeting("Добрый вечер, слушаю вас.") == "Слушаю вас."
    assert drop_greeting("Hello, which date?") == "Which date?"
    # Not a greeting at the start: untouched.
    assert drop_greeting("На какую дату? Здравствуйте") == "На какую дату? Здравствуйте"
    assert drop_greeting("Приветливый зал на втором этаже.") == "Приветливый зал на втором этаже."


async def test_a_streamed_greeting_is_cut_across_chunks() -> None:
    assert await _text("Здрав", "ствуйте! ", "На какую ", "дату?") == "На какую дату?"


async def test_a_reply_without_a_greeting_streams_unchanged() -> None:
    assert await _text("На какую ", "дату и ", "сколько гостей?") == "На какую дату и сколько гостей?"


async def test_a_reply_that_is_only_a_greeting_says_nothing() -> None:
    assert await _text("Здравствуйте!") == ""


async def test_tool_call_chunks_pass_through() -> None:
    call = llm.ChatChunk(id="c", delta=llm.ChoiceDelta(role="assistant", tool_calls=[]))

    async def stream() -> AsyncIterator[llm.ChatChunk]:
        yield call

    assert [chunk async for chunk in without_leading_greeting(stream())] == [call]


async def test_a_greeting_in_its_own_chunk_leaves_no_gap() -> None:
    assert await _text("Добрый день!", " чем могу ", "помочь?") == "Чем могу помочь?"


async def test_each_model_request_logs_the_prompt_ids(
    tools: BookingTools, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def fake_llm_node(*_: object) -> AsyncIterator[llm.ChatChunk]:
        yield _chunk("Which date?")

    monkeypatch.setattr(Agent.default, "llm_node", fake_llm_node)
    agent = BookingAgent(DialogFlow(AgentSettings(_env_file=None)), tools)

    with caplog.at_level(logging.DEBUG, logger="src.app.voice.agent"):
        async for _ in agent.llm_node(llm.ChatContext(), [], ModelSettings()):
            pass

    ids = next(r for r in caplog.records if r.getMessage() == "Sending the system prompt set").prompt_ids
    assert get_prompt("persona").id in ids
    assert get_prompt("tool_create_booking").id in ids
