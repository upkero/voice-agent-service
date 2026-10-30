"""What a call leaves behind in the metrics, driven through the real events.

The registry is process-global and shared by every test, so each case reads a
value before and after instead of expecting a clean slate.
"""

import logging
from typing import Any

import pytest
from livekit.agents import llm
from livekit.agents.metrics import AgentSessionUsage, LLMModelUsage, STTModelUsage, TTSModelUsage
from livekit.agents.voice.events import ConversationItemAddedEvent, SessionUsageUpdatedEvent
from prometheus_client import REGISTRY

from src.app.services.dialog.tools import BookingTools
from src.app.voice.call_stats import CallStats
from tests.fakes import TOMORROW


def _value(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _item(role: str, **report: Any) -> ConversationItemAddedEvent:
    message = llm.ChatMessage(role=role, content=["text"], metrics=report)  # type: ignore[arg-type]
    return ConversationItemAddedEvent(item=message)


def _usage(*models: Any) -> SessionUsageUpdatedEvent:
    return SessionUsageUpdatedEvent(usage=AgentSessionUsage(model_usage=list(models)))


# -- per-turn latency ---------------------------------------------------------
def test_a_replys_latency_lands_in_the_buckets_it_belongs_to() -> None:
    stats = CallStats("en")
    labels = {"stage": "e2e", "provider": "", "model": "", "after_tool": "false"}
    count, below, above = (
        _value("voice_turn_latency_seconds_count", **labels),
        _value("voice_turn_latency_seconds_bucket", le="1.0", **labels),
        _value("voice_turn_latency_seconds_bucket", le="1.5", **labels),
    )

    stats.on_conversation_item(_item("assistant", e2e_latency=1.2))

    assert _value("voice_turn_latency_seconds_count", **labels) == count + 1
    assert _value("voice_turn_latency_seconds_bucket", le="1.0", **labels) == below
    assert _value("voice_turn_latency_seconds_bucket", le="1.5", **labels) == above + 1


def test_stages_are_filed_with_the_provider_the_framework_names() -> None:
    stats = CallStats("en")
    stt = {"stage": "stt", "provider": "Deepgram", "model": "nova-3", "after_tool": "false"}
    ttft = {"stage": "llm_ttft", "provider": "openrouter.ai", "model": "m", "after_tool": "false"}
    before = [_value("voice_turn_latency_seconds_count", **labels) for labels in (stt, ttft)]

    stats.on_conversation_item(
        _item(
            "user",
            transcription_delay=0.3,
            stt_metadata={"model_provider": "Deepgram", "model_name": "nova-3"},
        )
    )
    stats.on_conversation_item(
        _item("assistant", llm_node_ttft=0.6, llm_metadata={"model_provider": "openrouter.ai", "model_name": "m"})
    )

    after = [_value("voice_turn_latency_seconds_count", **labels) for labels in (stt, ttft)]
    assert after == [count + 1 for count in before]
    assert stats.turns == 1


def test_the_reply_after_a_tool_is_marked_and_the_next_one_is_not() -> None:
    stats = CallStats("en")
    marked = {"stage": "e2e", "provider": "", "model": "", "after_tool": "true"}
    before = _value("voice_turn_latency_seconds_count", **marked)

    stats.on_tools_executed(object())
    stats.on_conversation_item(_item("assistant", e2e_latency=2.0))
    stats.on_conversation_item(_item("assistant", e2e_latency=1.0))

    assert _value("voice_turn_latency_seconds_count", **marked) == before + 1


def test_an_interrupted_reply_is_counted() -> None:
    stats = CallStats("en")
    before = _value("voice_interruptions_total", kind="interrupted")
    message = llm.ChatMessage(role="assistant", content=["text"], interrupted=True)

    stats.on_conversation_item(ConversationItemAddedEvent(item=message))

    assert stats.interruptions == 1
    assert _value("voice_interruptions_total", kind="interrupted") == before + 1


# -- usage --------------------------------------------------------------------
def test_usage_is_filed_as_the_difference_between_snapshots() -> None:
    """The event carries running totals; adding them up would count turn one twice."""
    stats = CallStats("en", deepgram_price_per_minute_usd=0.6)
    tokens = {"provider": "openrouter.ai", "model": "m-usage"}
    audio = {"provider": "Deepgram", "model": "nova-usage"}
    chars = {"provider": "openrouter.ai", "model": "tts-usage"}
    names = (
        ("voice_llm_tokens_total", {**tokens, "type": "input"}),
        ("voice_llm_tokens_total", {**tokens, "type": "cached_input"}),
        ("voice_llm_tokens_total", {**tokens, "type": "output"}),
        ("voice_stt_audio_seconds_total", audio),
        ("voice_cost_usd_total", {**audio, "component": "stt"}),
        ("voice_tts_characters_total", chars),
    )
    before = [_value(name, **labels) for name, labels in names]

    def snapshot(scale: int) -> SessionUsageUpdatedEvent:
        return _usage(
            LLMModelUsage(**tokens, input_tokens=100 * scale, input_cached_tokens=40 * scale, output_tokens=10 * scale),
            STTModelUsage(**audio, audio_duration=30.0 * scale),
            TTSModelUsage(**chars, characters_count=50 * scale),
        )

    stats.on_usage(snapshot(1))
    stats.on_usage(snapshot(2))

    added = [_value(name, **labels) - was for (name, labels), was in zip(names, before, strict=True)]
    # input is the uncached part: 60 per snapshot unit, 40 cached, 10 out.
    assert added == pytest.approx([120, 80, 20, 60.0, 0.6, 100])


# -- tools and the call's outcome --------------------------------------------
@pytest.fixture
def stats() -> CallStats:
    return CallStats("en")


@pytest.fixture
def counted(reservations: Any, state: Any, agent_settings: Any, stats: CallStats) -> BookingTools:
    return BookingTools(reservations, state, agent_settings, today=lambda: TOMORROW, stats=stats)


async def test_a_lookup_that_finds_nothing_is_counted_as_not_found(counted: BookingTools, stats: CallStats) -> None:
    labels = {"op": "find", "result": "not_found"}
    before = _value("voice_bookings_total", **labels)
    calls = _value("voice_tool_calls_total", tool="find_booking", result="ok")

    await counted.find_booking({"guest_name": "Nobody", "booking_date": TOMORROW.isoformat()})

    assert _value("voice_bookings_total", **labels) == before + 1
    assert _value("voice_tool_calls_total", tool="find_booking", result="ok") == calls + 1
    stats.pipeline_started = True
    assert stats.outcome == "lookup_only"


async def test_booking_a_table_makes_the_call_a_booked_one(counted: BookingTools, stats: CallStats) -> None:
    created = _value("voice_bookings_total", op="create", result="created")
    await counted.check_availability({"booking_date": TOMORROW.isoformat(), "party_size": 2, "preferred_time": "19:00"})

    result = await counted.create_booking(
        {"slot_ref": "slot_1", "guest_name": "Ivanov", "party_size": 2, "confirmed": True}
    )
    stats.pipeline_started = True

    assert result["ok"] is True
    assert _value("voice_bookings_total", op="create", result="created") == created + 1
    assert stats.outcome == "booked"


async def test_a_refused_call_is_not_a_booking_attempt(counted: BookingTools) -> None:
    failed = _value("voice_bookings_total", op="create", result="failed")

    await counted.create_booking({"slot_ref": "slot_1", "guest_name": "Ivanov", "party_size": 2, "confirmed": False})

    assert _value("voice_bookings_total", op="create", result="failed") == failed
    assert _value("voice_tool_calls_total", tool="create_booking", result="not_confirmed") >= 1


def test_a_call_whose_pipeline_never_started_is_a_failed_start() -> None:
    assert CallStats("en").outcome == "failed_start"


def test_finish_files_the_call_and_logs_one_line_without_a_name(caplog: pytest.LogCaptureFixture) -> None:
    ticks = iter([100.0, 142.5])
    stats = CallStats("ru", clock=lambda: next(ticks))
    stats.pipeline_started = True
    stats.note_guest("abc123abc123")
    calls = _value("voice_calls_total", outcome="no_action", language="ru")

    with caplog.at_level(logging.INFO):
        stats.finish(
            room="r", job_id="j", reason="done", degradations=["tts"], booking_refs=[], confirmation_spoken=False
        )

    assert _value("voice_calls_total", outcome="no_action", language="ru") == calls + 1
    record = next(r for r in caplog.records if r.getMessage() == "call.finished")
    assert (record.__dict__["outcome"], record.__dict__["duration_s"]) == ("no_action", 42.5)
    assert record.__dict__["guest_name_fp"] == "abc123abc123"
    assert record.__dict__["degradations"] == ["tts"]


def test_the_series_an_alert_reads_exist_at_zero_before_any_call() -> None:
    """increase() cannot see the first event of a series that appears already at 1."""
    from src.app.core.metrics import preinitialise

    preinitialise(("ru", "en"))

    assert REGISTRY.get_sample_value("voice_calls_total", {"outcome": "failed_start", "language": "ru"}) is not None
    assert REGISTRY.get_sample_value("voice_degradation_notices_total", {"kind": "tts"}) is not None
    for op, result in [("create", "created"), ("create", "failed"), ("cancel", "cancelled"), ("find", "not_found")]:
        assert REGISTRY.get_sample_value("voice_bookings_total", {"op": op, "result": result}) is not None, (op, result)


@pytest.mark.parametrize("tool", ["create_booking", "cancel_booking", "find_booking"])
@pytest.mark.parametrize(
    "result",
    [
        {"ok": True, "bookings": [{}]},
        {"ok": True},
        {"ok": False, "reason": "entity_not_found"},
        {"ok": False, "reason": "wrong_booking_code"},
        {"ok": False, "reason": "core_unavailable"},
    ],
)
def test_every_booking_result_a_call_can_file_is_zero_initialised(tool: str, result: dict[str, Any]) -> None:
    """A result missing from BOOKING_RESULTS would start at 1 and its first event would never show."""
    from src.app.core.metrics import BOOKING_RESULTS, BOOKINGS

    def totals() -> dict[tuple[str, str], float]:
        samples = BOOKINGS.collect()[0].samples
        return {(x.labels["op"], x.labels["result"]): x.value for x in samples if x.name.endswith("_total")}

    before = totals()

    CallStats("en").on_tool(tool, result, 0.1)

    changed = [labels for labels, value in totals().items() if value != before.get(labels)]
    assert len(changed) == 1
    op, filed = changed[0]
    assert filed in BOOKING_RESULTS[op], (op, filed)
