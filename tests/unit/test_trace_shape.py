"""The worker's spans as Tempo receives them: wrappers gone, children re-hung."""

from opentelemetry.instrumentation.utils import suppress_instrumentation
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.app.voice.tracing import CollapsingSpanProcessor


def _tracer() -> tuple[InMemorySpanExporter, TracerProvider]:
    exported = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(CollapsingSpanProcessor(SimpleSpanProcessor(exported)))
    return exported, provider


def test_a_sentence_hangs_directly_under_its_tts_node_and_names_the_voice() -> None:
    exported, provider = _tracer()
    tracer = provider.get_tracer("test")

    with (
        tracer.start_as_current_span("agent_turn"),
        tracer.start_as_current_span("tts_node") as node,
        tracer.start_as_current_span("tts_stream_adapter"),
        tracer.start_as_current_span("tts_request_run"),
        tracer.start_as_current_span("tts_fallback_adapter") as adapter,
        tracer.start_as_current_span("tts_request_run"),
    ):
        adapter.set_attribute("lk.tts.label", "FallbackAdapter")
        for label in ("primary", "fallback"):
            with (
                tracer.start_as_current_span("tts_request") as request,
                tracer.start_as_current_span("tts_request_run"),
            ):
                request.set_attribute("lk.tts.label", label)

    spans = {span.name: span for span in exported.get_finished_spans()}
    assert sorted(span.name for span in exported.get_finished_spans()) == [
        "agent_turn",
        "tts_node",
        "tts_request",
        "tts_request",
    ]
    assert spans["tts_request"].parent is not None
    assert spans["tts_request"].parent.span_id == node.get_span_context().span_id
    assert spans["tts_node"].parent is not None
    assert spans["tts_node"].parent.span_id == spans["agent_turn"].context.span_id
    assert (spans["tts_node"].attributes or {})["lk.tts.label"] == "fallback"


def test_a_dropped_root_leaves_its_children_as_roots() -> None:
    exported, provider = _tracer()
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("start_agent_activity"), tracer.start_as_current_span("on_enter"):
        pass

    (span,) = exported.get_finished_spans()
    assert (span.name, span.parent) == ("on_enter", None)


def test_spans_started_with_instrumentation_suppressed_are_not_exported() -> None:
    exported, provider = _tracer()

    with suppress_instrumentation(), provider.get_tracer("test").start_as_current_span("tts_request"):
        pass

    assert exported.get_finished_spans() == ()
