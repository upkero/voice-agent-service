"""The Prometheus instruments.

Cross-cutting like telemetry.py: the gateway, the dialogue and the voice layer all file into them.

prometheus_client rather than OTel metrics, because livekit runs one job per
process and the process exits when the call ends: an OTel counter would be a
new series per call and lose its last increments at exit. In multiprocess mode
(PROMETHEUS_MULTIPROC_DIR, which the worker's /metrics endpoint then sums) the
values live in files and outlast the process.

Labels are bounded sets and never carry a request id, room, guest name or any
free text.
"""

from prometheus_client import Counter, Histogram

# NOTE: one .db file per call (job pid) accumulates in PROMETHEUS_MULTIPROC_DIR;
# the directory is tmpfs, so a restart is the cleanup. Prune by age if it passes ~10k files.

_SECONDS = (0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8, 12)

# stage: stt | eou | llm_ttft | tts_ttfb | e2e. The framework computes every value
# (ChatMessage.metrics); this only files them. after_tool marks the reply that
# followed a tool call, which carries a second LLM pass the others do not.
TURN_LATENCY = Histogram(
    "voice_turn_latency_seconds",
    "Per-turn latency by stage, as measured by the framework.",
    ["stage", "provider", "model", "after_tool"],
    buckets=_SECONDS,
)
TOOL_CALLS = Counter("voice_tool_calls_total", "Booking tool calls by outcome.", ["tool", "result"])
TOOL_DURATION = Histogram(
    "voice_tool_duration_seconds", "Booking tool latency, filler speech excluded.", ["tool"], buckets=_SECONDS
)
OPS_CORE_DURATION = Histogram(
    "voice_ops_core_request_duration_seconds",
    "ops-core-api requests, one observation per attempt.",
    ["method", "route", "status_class"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
BOOKINGS = Counter("voice_bookings_total", "Booking operations by result.", ["op", "result"])
CALLS = Counter("voice_calls_total", "Finished calls by outcome.", ["outcome", "language"])
CALL_DURATION = Histogram(
    "voice_call_duration_seconds", "Call length.", ["outcome"], buckets=(15, 30, 60, 120, 180, 300, 600)
)
CALL_TURNS = Histogram("voice_call_turns", "Guest turns per call.", ["outcome"], buckets=(1, 2, 3, 5, 8, 13, 20, 30))
INTERRUPTIONS = Counter("voice_interruptions_total", "Interrupted replies.", ["kind"])
DEGRADATION_NOTICES = Counter(
    "voice_degradation_notices_total", "Times the guest was told the audio is broken.", ["kind"]
)
PROVIDER_FALLBACK = Counter(
    "voice_provider_fallback_total",
    "STT/TTS provider availability changes inside a fallback chain.",
    ["kind", "provider", "available"],
)
UNCONFIRMED_BOOKINGS = Counter(
    "voice_unconfirmed_bookings_total", "Calls that ended with a booking the guest never heard confirmed."
)
LLM_TOKENS = Counter(
    "voice_llm_tokens_total",
    "LLM tokens. type=input is the uncached part; cached_input is counted apart.",
    ["provider", "model", "type"],
)
STT_AUDIO_SECONDS = Counter("voice_stt_audio_seconds_total", "Audio sent to STT.", ["provider", "model"])
TTS_CHARACTERS = Counter("voice_tts_characters_total", "Characters sent to TTS.", ["provider", "model"])
COST_USD = Counter("voice_cost_usd_total", "Spend by component.", ["component", "provider", "model"])
