"""Мила, as LiveKit sees her.

Every tool here is a wrapper and nothing else: it takes the raw arguments the
model produced, hands them to `services/dialog/tools.py`, and returns what
comes back. No validation, no error handling and no phrasing lives at this
level, so the code the tests exercise is the same code the guest talks to —
the alternative is a tested dispatcher sitting behind an untested adapter that
quietly does something slightly different.

The schemas are passed with `raw_schema=` rather than inferred from type hints.
Inference is convenient and produces whatever JSON Schema the signature happens
to imply; for a tool that takes tables out of a restaurant's diary, what the
model is allowed to send should be written down where it can be read.
"""

import re
from collections.abc import AsyncIterable, AsyncIterator, Callable
from typing import Any

from livekit.agents import Agent, ModelSettings, RunContext, function_tool, llm

from src.app.messages import filler_line
from src.app.services.dialog.flow import DialogFlow
from src.app.services.dialog.tools import (
    CANCEL_BOOKING_SCHEMA,
    CHECK_AVAILABILITY_SCHEMA,
    CREATE_BOOKING_SCHEMA,
    FIND_BOOKING_SCHEMA,
    BookingTools,
)

# The opening line is played from cached audio before the model says anything,
# and however the prompt puts it, a guest's "hello" still draws a hello back now
# and then. A second greeting is never right on a call she opened, so it is cut
# here rather than asked for.
_GREETING = re.compile(
    r"^\s*(здравствуйте|здравствуй|добрый\s+(день|вечер)|доброе\s+утро|привет|hello|hi|good\s+(morning|afternoon|evening))"
    r"\b[\s!,.]*",
    re.IGNORECASE,
)
# Enough text to see a whole greeting ("Добрый вечер!") before deciding.
_LOOKAHEAD = 24


def drop_greeting(text: str) -> str:
    """The reply without a greeting at its start, its first letter capitalised."""
    rest = _GREETING.sub("", text, count=1)
    return rest[:1].upper() + rest[1:] if rest != text else text


async def without_leading_greeting(
    chunks: AsyncIterable[llm.ChatChunk | str | Any],
) -> AsyncIterator[llm.ChatChunk | str | Any]:
    """Hold back the first few characters of a reply until a greeting can be ruled out.

    Only the opening is buffered; once it is decided, text streams through
    untouched, so the guest waits for a couple of tokens, not for the reply.
    Tool calls and usage pass straight through.
    """
    held: list[llm.ChatChunk | str] = []
    text = ""
    decided = False
    cut = False

    def as_one(content: str) -> list[llm.ChatChunk | str]:
        # The held opening, re-emitted as a single piece of new text.
        if not content:
            return []
        if isinstance(held[0], str):
            return [content]
        return [held[0].model_copy(update={"delta": llm.ChoiceDelta(role="assistant", content=content)})]

    async for chunk in chunks:
        content = chunk if isinstance(chunk, str) else getattr(getattr(chunk, "delta", None), "content", None)
        if decided or not content:
            yield chunk
            continue
        held.append(chunk)
        text += content
        if len(text) < _LOOKAHEAD and not re.search(r"[!.?]", text):
            continue
        kept = drop_greeting(text)
        if not kept.strip():
            # The whole opening was the greeting ("Добрый день!"): look at what
            # follows the same way, and start it cleanly, without the gap.
            held, text, cut = [], "", True
            continue
        decided = True
        for out in _release(held, text, kept, cut, as_one):
            yield out
    if not decided and held:
        for out in _release(held, text, drop_greeting(text), cut, as_one):
            yield out


def _release(
    held: list[llm.ChatChunk | str],
    text: str,
    kept: str,
    cut: bool,
    as_one: Callable[[str], list[llm.ChatChunk | str]],
) -> list[llm.ChatChunk | str]:
    if kept == text and not cut:
        return held
    kept = kept.lstrip()
    return as_one(kept[:1].upper() + kept[1:])


class BookingAgent(Agent):
    def __init__(self, flow: DialogFlow, tools: BookingTools) -> None:
        super().__init__(instructions=flow.system_prompt())
        self._flow = flow
        self._booking_tools = tools

    @property
    def flow(self) -> DialogFlow:
        return self._flow

    async def llm_node(
        self, chat_ctx: llm.ChatContext, tools: list[llm.Tool], model_settings: ModelSettings
    ) -> AsyncIterator[llm.ChatChunk | str | Any]:
        async for chunk in without_leading_greeting(Agent.default.llm_node(self, chat_ctx, tools, model_settings)):
            yield chunk

    def _hold_the_line(self, ctx: RunContext[Any], tool: str) -> None:
        # Not awaited: the tool runs while the filler plays, so a slow ops-core
        # costs the guest no extra time, only no longer silence. Kept out of the
        # chat context so the model never treats it as something it said.
        ctx.session.say(filler_line(self._booking_tools.language, tool), add_to_chat_ctx=False)

    @function_tool(raw_schema=CHECK_AVAILABILITY_SCHEMA)
    async def check_availability(self, ctx: RunContext[Any], raw_arguments: dict[str, Any]) -> dict[str, Any]:
        self._hold_the_line(ctx, "check_availability")
        return await self._booking_tools.check_availability(raw_arguments)

    @function_tool(raw_schema=CREATE_BOOKING_SCHEMA)
    async def create_booking(self, ctx: RunContext[Any], raw_arguments: dict[str, Any]) -> dict[str, Any]:
        self._hold_the_line(ctx, "create_booking")
        return await self._booking_tools.create_booking(raw_arguments)

    @function_tool(raw_schema=FIND_BOOKING_SCHEMA)
    async def find_booking(self, ctx: RunContext[Any], raw_arguments: dict[str, Any]) -> dict[str, Any]:
        self._hold_the_line(ctx, "find_booking")
        return await self._booking_tools.find_booking(raw_arguments)

    @function_tool(raw_schema=CANCEL_BOOKING_SCHEMA)
    async def cancel_booking(self, ctx: RunContext[Any], raw_arguments: dict[str, Any]) -> dict[str, Any]:
        self._hold_the_line(ctx, "cancel_booking")
        return await self._booking_tools.cancel_booking(raw_arguments)
