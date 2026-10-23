"""OpenRouter's per-request cost, read off the stream the framework drops it from."""

from typing import Any

import pytest
from livekit.agents.types import NOT_GIVEN
from livekit.plugins import openai
from openai.types.chat import ChatCompletionChunk
from prometheus_client import REGISTRY

from src.app.core.settings.llm import LLMSettings
from src.app.llm.cost_tap import tap_openrouter_cost
from src.app.llm.factory import create_llm


def _chunk(usage: dict[str, Any] | None) -> ChatCompletionChunk:
    return ChatCompletionChunk.model_validate(
        {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": "m", "choices": [], "usage": usage}
    )


class _Stream:
    """What the SDK's AsyncStream is to the plugin: an async context manager over chunks."""

    def __init__(self, chunks: list[ChatCompletionChunk]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aenter__(self) -> "_Stream":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.closed = True

    async def __aiter__(self) -> Any:
        for chunk in self._chunks:
            yield chunk


def _spend(provider: str, model: str) -> float:
    labels = {"component": "llm", "provider": provider, "model": model}
    return REGISTRY.get_sample_value("voice_cost_usd_total", labels) or 0.0


def _settings(base_url: str) -> LLMSettings:
    return LLMSettings(  # type: ignore[call-arg]
        _env_file=None, provider="openai_compatible", model="vendor/model-x", base_url=base_url, api_key="k"
    )


async def test_the_cost_on_the_usage_chunk_is_filed_and_the_stream_is_unchanged() -> None:
    llm = create_llm(_settings("https://openrouter.ai/api/v1"))
    assert isinstance(llm, openai.LLM)
    usage = {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10, "cost": 0.0042}
    stream = _Stream([_chunk(None), _chunk(usage)])

    async def create(*args: Any, **kwargs: Any) -> _Stream:
        return stream

    llm._client.chat.completions.create = create  # type: ignore[method-assign]
    tap_openrouter_cost(llm, "vendor/model-x")
    before = _spend(llm.provider, "vendor/model-x")

    seen = []
    async with await llm._client.chat.completions.create(model="m") as tapped:
        async for chunk in tapped:
            seen.append(chunk)

    assert len(seen) == 2
    assert stream.closed
    assert _spend(llm.provider, "vendor/model-x") == pytest.approx(before + 0.0042)


def test_only_openrouter_is_asked_for_cost_and_tapped() -> None:
    routed = create_llm(_settings("https://openrouter.ai/api/v1"))
    local = create_llm(_settings("http://ollama:11434/v1"))
    assert isinstance(routed, openai.LLM) and isinstance(local, openai.LLM)

    assert routed._opts.extra_body == {"usage": {"include": True}}
    assert local._opts.extra_body is NOT_GIVEN
    assert routed._client.chat.completions.create.__name__ == "create_tapped"
    assert local._client.chat.completions.create.__name__ != "create_tapped"
