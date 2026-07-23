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

from typing import Any

from livekit.agents import Agent, RunContext, function_tool

from src.app.services.dialog.flow import BaseDialogFlow
from src.app.services.dialog.tools import (
    CANCEL_BOOKING_SCHEMA,
    CHECK_AVAILABILITY_SCHEMA,
    CREATE_BOOKING_SCHEMA,
    FIND_BOOKING_SCHEMA,
    BookingTools,
)


class BookingAgent(Agent):
    def __init__(self, flow: BaseDialogFlow, tools: BookingTools) -> None:
        super().__init__(instructions=flow.system_prompt())
        self._flow = flow
        self._booking_tools = tools

    @property
    def flow(self) -> BaseDialogFlow:
        return self._flow

    @function_tool(raw_schema=CHECK_AVAILABILITY_SCHEMA)
    async def check_availability(self, ctx: RunContext, raw_arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._booking_tools.check_availability(raw_arguments)

    @function_tool(raw_schema=CREATE_BOOKING_SCHEMA)
    async def create_booking(self, ctx: RunContext, raw_arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._booking_tools.create_booking(raw_arguments)

    @function_tool(raw_schema=FIND_BOOKING_SCHEMA)
    async def find_booking(self, ctx: RunContext, raw_arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._booking_tools.find_booking(raw_arguments)

    @function_tool(raw_schema=CANCEL_BOOKING_SCHEMA)
    async def cancel_booking(self, ctx: RunContext, raw_arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._booking_tools.cancel_booking(raw_arguments)
