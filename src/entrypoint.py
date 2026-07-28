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
from src.app.core.settings.agent import get_agent_settings
from src.app.core.settings.livekit import get_livekit_settings
from src.app.core.settings.logging import get_logging_settings
from src.app.voice.confirmation import ConfirmationTracker
from src.app.voice.degradation import DegradationNotice
from src.app.voice.session import build_agent, build_session, greet, register_degradation_notices, start_session

logger = getLogger(__name__)


def prewarm(proc: JobProcess) -> None:
    """Load what every call needs, once per process.

    Voice activity detection is loaded here rather than per job because it is
    the same model for every call, and loading it on the first one costs a
    second the guest spends listening to nothing.
    """
    setup_logging(get_logging_settings())
    proc.userdata["vad"] = silero.VAD.load()
    logger.info("Worker prewarmed", extra={"language": get_agent_settings().language})


async def entrypoint(ctx: JobContext) -> None:
    """Handle one call.

    Returns as soon as the conversation is running: the framework keeps the job
    alive until the room closes and then runs the shutdown callback. Blocking
    here instead would hold a worker slot for the length of every call for no
    reason.
    """
    container = ApplicationContainer()
    room_name = ctx.room.name
    agent, state = build_agent(container, room_name)
    notice = DegradationNotice(ctx.room)
    tracker = ConfirmationTracker(state)
    session = build_session(vad=ctx.proc.userdata.get("vad"))

    async def on_shutdown(reason: str = "") -> None:
        tracker.report(room_name, reason)
        await session.aclose()
        await container.close()
        logger.info("Call ended", extra={"room": room_name, "reason": reason})

    ctx.add_shutdown_callback(on_shutdown)

    await ctx.connect()
    session.on("conversation_item_added", tracker.on_conversation_item)
    register_degradation_notices(session, notice, get_agent_settings().language)

    await start_session(session, agent, ctx, notice)
    await greet(session, agent, notice, ctx.room)
    logger.info("Call started", extra={"room": room_name})


if __name__ == "__main__":
    livekit_settings = get_livekit_settings()
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            ws_url=livekit_settings.url,
            api_key=livekit_settings.api_key,
            api_secret=livekit_settings.api_secret.get_secret_value(),
        )
    )
