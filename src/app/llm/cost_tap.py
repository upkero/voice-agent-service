"""OpenRouter says what each request cost; the framework throws it away.

The usage block of the last streamed chunk carries `cost` in dollars, but
livekit copies only the token counts into its own CompletionUsage. So the number
is read off the client's stream before the framework sees it, and filed into
voice_cost_usd_total as the exact spend rather than tokens times a price table
that would go stale with every model switch.
"""

from collections.abc import AsyncIterator, Callable
from typing import Any

from livekit.plugins import openai

from src.app.core.metrics import COST_USD


class _TappedStream:
    """The SDK's AsyncStream, with every chunk's usage.cost reported on its way through."""

    def __init__(self, stream: Any, on_cost: Callable[[float], None]) -> None:
        self._stream = stream
        self._on_cost = on_cost

    async def __aenter__(self) -> "_TappedStream":
        await self._stream.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._stream.__aexit__(*exc)

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._chunks()

    async def _chunks(self) -> AsyncIterator[Any]:
        async for chunk in self._stream:
            # The SDK's models keep fields they do not declare, which is the only
            # reason `cost` is readable here at all.
            cost = getattr(chunk.usage, "cost", None)
            if isinstance(cost, int | float):
                self._on_cost(float(cost))
            yield chunk

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def tap_openrouter_cost(llm: openai.LLM, model: str) -> None:
    """Report each request's cost, labelled like the framework labels its tokens."""
    # NOTE: reaches into the plugin's client, because it is built inside the plugin and
    # the plugin has no hook for a response. Pinned by the lockfile; the test in
    # test_cost_tap.py fails first if a plugin update moves it.
    completions = llm._client.chat.completions
    create = completions.create

    async def create_tapped(*args: Any, **kwargs: Any) -> Any:
        stream = await create(*args, **kwargs)
        return _TappedStream(stream, lambda cost: COST_USD.labels("llm", llm.provider, model).inc(cost))

    completions.create = create_tapped  # type: ignore[method-assign]
