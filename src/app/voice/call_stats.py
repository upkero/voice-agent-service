"""What happened on one call, for the metrics and for one log line.

Owned by the job, fed by the session's events and by BookingTools, and reported
at shutdown next to ConfirmationTracker. The numbers are the framework's own
(ChatMessage.metrics, session usage), not recomputed here: a second clock would
disagree with the one in the trace.
"""

import time
from collections.abc import Callable, Sequence
from logging import getLogger
from typing import Any

from livekit.agents import AgentSession, llm
from livekit.agents.llm import MetricsReport

from src.app.core import metrics

logger = getLogger(__name__)

# Not a booking attempt: refused before anything was asked of ops-core-api.
_NOT_ATTEMPTED = frozenset({"invalid_arguments", "not_confirmed"})
_MISSING = frozenset({"entity_not_found", "unknown_reference"})


def _labels(metadata: Any) -> tuple[str, str]:
    metadata = metadata or {}
    return metadata.get("model_provider") or "", metadata.get("model_name") or ""


class CallStats:
    def __init__(
        self,
        language: str,
        *,
        deepgram_price_per_minute_usd: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._language = language
        self._deepgram_price = deepgram_price_per_minute_usd
        self._clock = clock
        self._started_at = clock()
        self._after_tool = False
        self._usage_seen: dict[tuple[str, str, str, str], float] = {}
        self.pipeline_started = False
        self._guest_name_fp = ""
        self.turns = 0
        self.interruptions = 0
        self.fallbacks = 0
        self._booked = self._cancelled = self._looked_up = False

    # -- tools ------------------------------------------------------------
    def note_guest(self, fingerprint: str) -> None:
        self._guest_name_fp = fingerprint

    def on_tool(self, tool: str, result: dict[str, Any], seconds: float) -> None:
        reason = "" if result["ok"] else str(result["reason"])
        metrics.TOOL_DURATION.labels(tool).observe(seconds)
        metrics.TOOL_CALLS.labels(tool, "ok" if result["ok"] else reason).inc()
        if reason in _NOT_ATTEMPTED:
            return
        if tool == "create_booking":
            self._booked = self._booked or result["ok"]
            self._booking("create", "created" if result["ok"] else "failed")
        elif tool == "cancel_booking":
            self._cancelled = self._cancelled or result["ok"]
            self._booking("cancel", "cancelled" if result["ok"] else self._failure(reason))
        elif tool == "find_booking":
            self._looked_up = True
            found = "found" if result.get("bookings") else "not_found"
            self._booking("find", found if result["ok"] else self._failure(reason))

    @staticmethod
    def _booking(op: str, result: str) -> None:
        metrics.BOOKINGS.labels(op, result).inc()

    @staticmethod
    def _failure(reason: str) -> str:
        if reason == "wrong_booking_code":
            return "wrong_code"
        return "not_found" if reason in _MISSING else "failed"

    # -- session events -----------------------------------------------------
    def attach(self, session: AgentSession[Any]) -> None:
        session.on("conversation_item_added", self.on_conversation_item)
        session.on("function_tools_executed", self.on_tools_executed)
        session.on("agent_false_interruption", self.on_false_interruption)
        session.on("session_usage_updated", self.on_usage)
        if session.stt is not None:
            session.stt.on("stt_availability_changed", lambda e: self.on_availability("stt", e.stt, e.available))
        if session.tts is not None:
            session.tts.on("tts_availability_changed", lambda e: self.on_availability("tts", e.tts, e.available))

    def on_conversation_item(self, event: Any) -> None:
        item = event.item
        if not isinstance(item, llm.ChatMessage):
            return
        report: MetricsReport = item.metrics
        if item.role == "user":
            self.turns += 1
            self._observe("stt", report.get("transcription_delay"), report.get("stt_metadata"))
            self._observe("eou", report.get("end_of_turn_delay"))
        elif item.role == "assistant":
            self._observe("llm_ttft", report.get("llm_node_ttft"), report.get("llm_metadata"))
            self._observe("tts_ttfb", report.get("tts_node_ttfb"), report.get("tts_metadata"))
            self._observe("e2e", report.get("e2e_latency"))
            self._after_tool = False
            if item.interrupted:
                self.interruptions += 1
                metrics.INTERRUPTIONS.labels("interrupted").inc()

    def _observe(self, stage: str, seconds: float | None, metadata: Any = None) -> None:
        if seconds is None:
            return
        provider, model = _labels(metadata)
        metrics.TURN_LATENCY.labels(stage, provider, model, str(self._after_tool).lower()).observe(seconds)

    def on_tools_executed(self, event: Any) -> None:
        # The next assistant message is the reply to the tool result. The filler
        # said while the tool ran is a separate speech with no message of its own.
        self._after_tool = True

    def on_false_interruption(self, event: Any) -> None:
        metrics.INTERRUPTIONS.labels("false_interruption").inc()

    def on_availability(self, kind: str, member: Any, available: bool) -> None:
        metrics.PROVIDER_FALLBACK.labels(kind, member.provider, str(available).lower()).inc()
        if not available:
            self.fallbacks += 1

    def on_usage(self, event: Any) -> None:
        """The event carries running totals for the session, so file the difference."""
        for usage in event.usage.model_usage:
            provider, model = usage.provider, usage.model
            if usage.type == "llm_usage":
                cached = self._added(usage, "input_cached_tokens")
                metrics.LLM_TOKENS.labels(provider, model, "cached_input").inc(cached)
                metrics.LLM_TOKENS.labels(provider, model, "input").inc(self._added(usage, "input_tokens") - cached)
                metrics.LLM_TOKENS.labels(provider, model, "output").inc(self._added(usage, "output_tokens"))
            elif usage.type == "stt_usage":
                seconds = self._added(usage, "audio_duration")
                metrics.STT_AUDIO_SECONDS.labels(provider, model).inc(seconds)
                if provider.lower() == "deepgram":
                    metrics.COST_USD.labels("stt", provider, model).inc(seconds / 60 * self._deepgram_price)
            elif usage.type == "tts_usage":
                metrics.TTS_CHARACTERS.labels(provider, model).inc(self._added(usage, "characters_count"))

    def _added(self, usage: Any, field: str) -> float:
        key = (usage.type, usage.provider, usage.model, field)
        total = float(getattr(usage, field))
        added = total - self._usage_seen.get(key, 0.0)
        self._usage_seen[key] = total
        return added

    # -- the end ------------------------------------------------------------
    @property
    def outcome(self) -> str:
        if not self.pipeline_started:
            return "failed_start"
        # A call that books and cancels (a reschedule) is counted as booked.
        if self._booked:
            return "booked"
        if self._cancelled:
            return "cancelled"
        return "lookup_only" if self._looked_up else "no_action"

    def finish(
        self,
        *,
        room: str,
        job_id: str,
        reason: str,
        degradations: Sequence[str],
        booking_refs: Sequence[str],
        confirmation_spoken: bool,
    ) -> None:
        outcome, duration = self.outcome, self._clock() - self._started_at
        metrics.CALLS.labels(outcome, self._language).inc()
        metrics.CALL_DURATION.labels(outcome).observe(duration)
        metrics.CALL_TURNS.labels(outcome).observe(self.turns)
        logger.info(
            "call.finished",
            extra={
                "room": room,
                "job_id": job_id,
                "outcome": outcome,
                "duration_s": round(duration, 1),
                "turns": self.turns,
                "interruptions": self.interruptions,
                "fallbacks": self.fallbacks,
                "degradations": list(degradations),
                "booking_refs": list(booking_refs),
                "confirmation_spoken": confirmation_spoken,
                "guest_name_fp": self._guest_name_fp,
                "shutdown_reason": reason,
            },
        )
