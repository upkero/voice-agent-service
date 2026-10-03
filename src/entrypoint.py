"""The agent worker process.

Run with:  python -m src.entrypoint dev      (local, hot reload)
           python -m src.entrypoint start    (production)

Separate from the HTTP process because the two have nothing to share. This one
holds a Whisper model and a voice in memory and is busy for the length of a
phone call; the other answers a request in a millisecond. Scaling them together
would mean sizing the API for the agent's memory, or the agent for the API's
concurrency, and neither is a trade worth making.
"""

from logging import getLogger

from livekit.agents import JobContext, JobProcess, WorkerOptions, cli
from livekit.plugins import silero

from src.app.bootstrap.container import ApplicationContainer
from src.app.core.logging import setup_logging
from src.app.core.request_id import set_request_id
from src.app.core.settings.agent import get_agent_settings
from src.app.core.settings.livekit import get_livekit_settings
from src.app.core.settings.logging import get_logging_settings
from src.app.core.settings.stt import get_stt_settings
from src.app.core.settings.tts import get_tts_settings
from src.app.voice.confirmation import ConfirmationTracker
from src.app.voice.degradation import DegradationNotice
from src.app.voice.greeting_audio import prewarm_greetings_in_background
from src.app.voice.session import build_agent, build_session, greet, register_degradation_notices, start_session

logger = getLogger(__name__)


def prewarm(proc: JobProcess) -> None:
    """Load what every call needs, once per process.

    Voice activity detection and the Whisper model are loaded here rather than per
    job because they are the same for every call, and loading them on the first
    one costs the guest seconds of silence.
    """
    setup_logging(get_logging_settings())
    proc.userdata["vad"] = silero.VAD.load()
    stt_settings = get_stt_settings()
    if "faster_whisper" in (stt_settings.provider, stt_settings.fallback_provider):
        # Same reasoning as the VAD: the model is identical for every call, and
        # loading it on the first utterance costs the guest ~10 s of silence.
        from src.app.llm.faster_whisper_stt_client import load_model

        load_model(stt_settings.model, stt_settings.compute_type)
    prewarm_greetings_in_background(get_agent_settings(), get_tts_settings())
    logger.info("Worker prewarmed", extra={"language": get_agent_settings().language})


async def entrypoint(ctx: JobContext) -> None:
    """Handle one call.

    Returns as soon as the conversation is running: the framework keeps the job
    alive until the room closes and then runs the shutdown callback. Blocking
    here instead would hold a worker slot for the length of every call for no
    reason.
    """
    room_name = ctx.room.name
    # No HTTP request wraps a job, so the ContextVar the outbound client reads
    # is empty and every call this worker makes to ops-core-api would arrive
    # uncorrelated — half this service's traffic. The room name is the natural
    # key: it is already in every log line on this side, and one call is one
    # room, so it joins the two sides of a booking without inventing an id
    # nobody can look up.
    set_request_id(f"room-{room_name}")

    # The language is a property of the call, not of the process: the token the
    # guest joined with carries it as a participant attribute. So the room has to
    # be joined, and the guest awaited, before the pipeline (whose STT hint and
    # TTS voice depend on it) can be built.
    await ctx.connect()
    participant = await ctx.wait_for_participant()
    call_settings = get_agent_settings().for_language(participant.attributes.get("language"))
    language = call_settings.language

    container = ApplicationContainer(call_settings)
    agent, state = build_agent(container, room_name)
    notice = DegradationNotice(ctx.room)
    tracker = ConfirmationTracker(state)
    session = build_session(language, vad=ctx.proc.userdata.get("vad"))

    async def on_shutdown(reason: str = "") -> None:
        tracker.report(room_name, reason)
        await session.aclose()
        await container.close()
        logger.info("Call ended", extra={"room": room_name, "reason": reason})

    ctx.add_shutdown_callback(on_shutdown)

    session.on("conversation_item_added", tracker.on_conversation_item)
    register_degradation_notices(session, notice, language)

    await start_session(session, agent, ctx, notice, language)
    await greet(session, agent, notice, ctx.room, language)
    logger.info("Call started", extra={"room": room_name, "language": language})


if __name__ == "__main__":
    livekit_settings = get_livekit_settings()
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            # prewarm loads Silero and Whisper; a replacement process spawned
            # while a call is using the CPU can take longer than the 10 s default.
            # Past it, livekit-agents kills the process with SIGUSR1, which is
            # logged as "process exited with non-zero exit code -10".
            initialize_process_timeout=60.0,
            ws_url=livekit_settings.url,
            api_key=livekit_settings.api_key,
            api_secret=livekit_settings.api_secret.get_secret_value(),
        )
    )
