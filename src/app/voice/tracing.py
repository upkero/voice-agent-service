"""The worker's trace shape: a call reads as turns, then model, tool and speech.

livekit wraps every model and speech request in adapter and retry spans, so one
spoken sentence sits six levels deep and more than half of a call's spans say
nothing a reader needs. CollapsingSpanProcessor drops those wrappers before
export and hangs their children on the nearest span that is kept, which leaves
`agent_turn > tts_node > tts_request` and `agent_turn > llm_node > llm_request`.
"""

import os

from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.utils import is_instrumentation_enabled
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanContext

from src.app.core.telemetry import setup_tracing

# The TTS retry attempt, the fallback and stream adapters around the provider's own
# request, and the activity wrappers around on_enter / on_exit. Each request's error
# is also recorded on the kept span around it, so none is lost. llm_request_run stays:
# portfolio-deploy's collector counts it into the voice LLM error metric.
WRAPPERS = frozenset(
    {
        "tts_stream_adapter",
        "tts_request_run",
        "tts_fallback_adapter",
        "start_agent_activity",
        "drain_agent_activity",
    }
)
_TTS_LABEL = "lk.tts.label"


class CollapsingSpanProcessor(SpanProcessor):
    """Forward every span but the WRAPPERS to `delegate`, re-parented past them.

    A processor rather than an exporter: spans are exported children first, so an
    exporter meets a child before it can know its parent will be dropped. A span
    starts after its parent, so at start the dropped parents are already known.
    Spans started with instrumentation suppressed (the greeting prewarm, which runs
    outside any call) are dropped too: livekit does not honour the flag itself.
    """

    def __init__(self, delegate: SpanProcessor) -> None:
        self._delegate = delegate
        # NOTE: never pruned. A job process serves one call and then exits, so this
        # holds a few hundred ids at most. A process that served many calls would
        # have to drop a trace's entries when its root ends.
        self._skip: dict[int, SpanContext | None] = {}
        self._tts_nodes: dict[int, Span] = {}

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        span_id = span.context.span_id
        parent = span.parent
        if parent is not None and parent.span_id in self._skip:
            # Resolved when the parent started, so one lookup skips any run of wrappers.
            parent = self._skip[parent.span_id]
            # NOTE: the SDK has no public way to change a span's parent. Done at start,
            # before any processor sees the span end.
            span._parent = parent
        if span.name in WRAPPERS or not is_instrumentation_enabled():
            self._skip[span_id] = parent
            return
        if span.name == "tts_node":
            self._tts_nodes[span_id] = span
        self._delegate.on_start(span, parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        span_id = span.context.span_id
        if span_id in self._skip:
            return
        self._tts_nodes.pop(span_id, None)
        label = (span.attributes or {}).get(_TTS_LABEL)
        node = self._tts_nodes.get(span.parent.span_id) if span.parent else None
        # The provider that actually spoke: after a failover, the last request to end.
        if label and node is not None and node.is_recording():
            node.set_attribute(_TTS_LABEL, label)
        self._delegate.on_end(span)

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._delegate.force_flush(timeout_millis)


def setup_worker_tracing() -> TracerProvider:
    """`setup_tracing`, with the export going through CollapsingSpanProcessor.

    setup_tracing (the same file in every service) attaches a plain exporter whenever
    OTEL_EXPORTER_OTLP_ENDPOINT is set, so the variable is hidden from it for the
    one call and the worker attaches its own.
    """
    endpoint = os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    try:
        provider = setup_tracing("voice-agent", "voice-agent-service")
    finally:
        if endpoint is not None:
            os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = endpoint
    if endpoint:
        provider.add_span_processor(CollapsingSpanProcessor(BatchSpanProcessor(OTLPSpanExporter())))
    return provider
