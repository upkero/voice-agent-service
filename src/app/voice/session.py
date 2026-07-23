"""Assembling one call.

This is the composition root of the worker process: the only place where
settings, factories, services and the LiveKit session meet. Everything it wires
together was built without knowing this file exists, which is the test of
whether the layering held.
"""

from logging import getLogger

from livekit import rtc
from livekit.agents import Agent, AgentSession, JobContext, RoomInputOptions

from src.app.bootstrap.container import ApplicationContainer
from src.app.core.settings.agent import get_agent_settings
from src.app.core.settings.llm import get_llm_settings
from src.app.core.settings.stt import get_stt_settings
from src.app.core.settings.tts import get_tts_settings
from src.app.llm.factory import create_llm
from src.app.llm.stt_factory import create_stt
from src.app.llm.tts_factory import create_tts
from src.app.services.dialog.session_state import DialogSessionState
from src.app.services.dialog.tools import BookingTools
from src.app.voice.agent import BookingAgent
from src.app.voice.degradation import DegradationNotice, degradation_message

logger = getLogger(__name__)


def build_agent(container: ApplicationContainer, room_id: str) -> tuple[BookingAgent, DialogSessionState]:
    """One agent and one reference table per call.

    The state is per room and never shared: references issued to one guest must
    not resolve for another, and a process-wide table is how they would.
    """
    agent_settings = get_agent_settings()
    state = DialogSessionState(room_id)
    tools = BookingTools(container.reservation_service, state, agent_settings)
    return BookingAgent(container.dialog_flow, tools), state


def build_session(vad: object | None = None) -> AgentSession:
    agent_settings = get_agent_settings()

    session = AgentSession(
        vad=vad,
        stt=create_stt(get_stt_settings(), agent_settings.language),
        llm=create_llm(get_llm_settings()),
        tts=create_tts(get_tts_settings(), agent_settings.language),
        # A guest interrupting the agent mid-sentence is normal on a phone call
        # ("no, the Friday one") — allowing it is what makes the exchange feel
        # like a conversation rather than a menu tree.
        allow_interruptions=True,
        # Two tool steps per turn: check availability, then answer. Booking is a
        # separate turn by design, because the guest has to agree in between.
        max_tool_steps=2,
    )
    return session


async def start_session(
    session: AgentSession,
    agent: Agent,
    ctx: JobContext,
    notice: DegradationNotice,
) -> None:
    """Start the pipeline, and keep the call alive if the audio path fails.

    A failure here is the worst-timed one there is: the guest has connected and
    is waiting. Falling through to text keeps a usable conversation instead of
    an empty room, because everything below the audio layer — the LLM, the
    tools, the booking — still works.
    """
    language = get_agent_settings().language
    try:
        await session.start(
            agent=agent,
            room=ctx.room,
            room_input_options=RoomInputOptions(
                # Text input is not only a fallback. It is what makes the
                # degraded path an actual conversation: typed messages enter the
                # same LLM and the same tools, so a guest with no working
                # microphone can still book a table.
                text_enabled=True,
                audio_enabled=True,
            ),
        )
    except Exception:
        logger.exception("Voice pipeline failed to start; continuing in text mode")
        await notice.announce_once("startup", degradation_message(language, "startup"))
        raise


async def greet(session: AgentSession, agent: BookingAgent, notice: DegradationNotice, room: rtc.Room) -> None:
    """Speak first.

    A voice agent that waits for the guest produces the silence-after-connect
    that people hang up on. If the greeting cannot be spoken, the same opening
    goes out as text — the call still starts.
    """
    language = get_agent_settings().language
    try:
        await session.generate_reply(instructions=agent.flow.greeting())
    except Exception:
        logger.exception("Could not deliver the greeting by voice")
        await notice.announce_once("tts", degradation_message(language, "tts"))
