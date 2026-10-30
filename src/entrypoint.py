"""The agent worker process.

Run with:  python -m src.entrypoint dev      (local, hot reload)
           python -m src.entrypoint start    (production)

Separate from the HTTP process because the two have nothing to share. This one
holds speech models (VAD, turn detection, any local STT or TTS) in memory and
is busy for the length of a phone call; the other answers a request in a millisecond. Scaling them together
would mean sizing the API for the agent's memory, or the agent for the API's
concurrency, and neither is a trade worth making.
"""

from logging import getLogger
from typing import get_args

from livekit.agents import JobContext, JobProcess, WorkerOptions, cli, telemetry
from livekit.plugins import silero
from opentelemetry import trace

from src.app.bootstrap.container import ApplicationContainer
from src.app.core.logging import setup_logging
from src.app.core.metrics import preinitialise
from src.app.core.request_id import set_request_id
from src.app.core.settings.agent import AgentLanguage, get_agent_settings
from src.app.core.settings.livekit import get_livekit_settings
from src.app.core.settings.logging import get_logging_settings
from src.app.core.settings.stt import get_stt_settings
from src.app.core.settings.tts import get_tts_settings
from src.app.core.settings.worker import get_worker_settings
from src.app.voice.call_stats import CallStats
from src.app.voice.confirmation import ConfirmationTracker
from src.app.voice.degradation import DegradationNotice
from src.app.voice.greeting_audio import prewarm_greetings_in_background
from src.app.voice.session import (
    build_agent,
    build_session,
    greet,
    native_turn_model_supported,
    register_degradation_notices,
    start_session,
)
from src.app.voice.tracing import setup_worker_tracing

logger = getLogger(__name__)


def prewarm(proc: JobProcess) -> None:
    """Load what every call needs, once per process.

    Voice activity detection and a local Whisper model (when faster_whisper is
    the primary or the fallback STT) are loaded here rather than per job because
    they are the same for every call, and loading them on the first one costs the
    guest seconds of silence.
    """
    setup_logging(get_logging_settings())
    # Here, not at import: under forkserver an import-time provider would be
    # built in the parent and its export thread would not survive the fork. Not
    # in entrypoint either: by then the framework's job_entrypoint span has
    # started on a no-op tracer. livekit gets the provider too, because its
    # shutdown flushes only the one it holds; otherwise a call's last spans die
    # with its process.
    telemetry.set_tracer_provider(setup_worker_tracing())
    # After livekit has emptied the multiprocess directory, which it does when the worker
    # starts and before the first process: earlier, these zeros would be deleted with it.
    preinitialise(get_args(AgentLanguage))
    proc.userdata["vad"] = silero.VAD.load()
    stt_settings = get_stt_settings()
    if "faster_whisper" in (stt_settings.provider, stt_settings.fallback_provider):
        # Same reasoning as the VAD: the model is identical for every call, and
        # loading it on the first utterance costs the guest ~10 s of silence.
        from src.app.llm.faster_whisper_stt_client import load_model

        load_model(stt_settings.model_for("faster_whisper"), stt_settings.compute_type)
    prewarm_greetings_in_background(get_agent_settings(), get_tts_settings())
    logger.info("Worker prewarmed", extra={"language": get_agent_settings().language})


def bind_call(room_name: str) -> None:
    """Stamp this process's logs, outbound requests and spans with the call.

    No HTTP request wraps a job, so the ContextVar the outbound client reads
    is empty and every call this worker makes to ops-core-api would arrive
    uncorrelated — half this service's traffic. The room name is the natural
    key: it is already in every log line on this side, and one call is one
    room, so it joins the two sides of a booking without inventing an id
    nobody can look up. A process serves one job, so the span metadata never
    leaks into another call.
    """
    request_id = f"room-{room_name}"
    set_request_id(request_id)
    telemetry.set_tracer_provider(
        trace.get_tracer_provider(),
        metadata={"request_id": request_id, "lk.room_name": room_name},
    )


async def entrypoint(ctx: JobContext) -> None:
    """Handle one call.

    Returns as soon as the conversation is running: the framework keeps the job
    alive until the room closes and then runs the shutdown callback. Blocking
    here instead would hold a worker slot for the length of every call for no
    reason.
    """
    room_name = ctx.room.name
    bind_call(room_name)

    # The language is a property of the call, not of the process: the token the
    # guest joined with carries it as a participant attribute. So the room has to
    # be joined, and the guest awaited, before the pipeline (whose STT hint and
    # TTS voice depend on it) can be built.
    await ctx.connect()
    participant = await ctx.wait_for_participant()
    call_settings = get_agent_settings().for_language(participant.attributes.get("language"))
    language = call_settings.language

    stats = CallStats(
        language, deepgram_price_per_minute_usd=get_stt_settings().deepgram_price_per_minute_usd
    )
    container = ApplicationContainer(call_settings)
    agent, state = build_agent(container, room_name, stats)
    notice = DegradationNotice(ctx.room)
    tracker = ConfirmationTracker(state)
    session = build_session(language, vad=ctx.proc.userdata.get("vad"))

    async def on_shutdown(reason: str = "") -> None:
        tracker.report(room_name, reason)
        await session.aclose()
        await container.close()
        stats.finish(
            room=room_name,
            job_id=ctx.job.id,
            reason=reason,
            degradations=notice.announced,
            booking_refs=state.booking_refs(),
            confirmation_spoken=tracker.confirmation_spoken,
        )
        logger.info("Call ended", extra={"room": room_name, "reason": reason})

    ctx.add_shutdown_callback(on_shutdown)

    session.on("conversation_item_added", tracker.on_conversation_item)
    stats.attach(session)
    register_degradation_notices(session, notice, language)

    await start_session(session, agent, ctx, notice, language)
    stats.pipeline_started = True
    await greet(session, agent, notice, ctx.room, language)
    logger.info("Call started", extra={"room": room_name, "language": language})


if __name__ == "__main__":
    livekit_settings = get_livekit_settings()
    worker_settings = get_worker_settings()
    options = WorkerOptions(
        entrypoint_fnc=entrypoint,
        prewarm_fnc=prewarm,
        # prewarm loads Silero and any local Whisper; a replacement process spawned
        # while a call is using the CPU can take longer than the 10 s default.
        # Past it, livekit-agents kills the process with SIGUSR1, which is
        # logged as "process exited with non-zero exit code -10".
        initialize_process_timeout=60.0,
        num_idle_processes=worker_settings.num_idle_processes,
        job_memory_limit_mb=worker_settings.job_memory_limit_mb,
        ws_url=livekit_settings.url,
        api_key=livekit_settings.api_key,
        api_secret=livekit_settings.api_secret.get_secret_value(),
    )
    if worker_settings.prometheus_port:
        options.prometheus_port = worker_settings.prometheus_port
    if not native_turn_model_supported():
        # The forkserver preloads livekit's native turn model, which crashes
        # without AVX2; spawn skips that preload.
        options.multiprocessing_context = "spawn"
    cli.run_app(options)
