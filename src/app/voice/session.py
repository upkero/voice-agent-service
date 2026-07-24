"""Assembling one call.

This is the composition root of the worker process: the only place where
settings, factories, services and the LiveKit session meet. Everything it wires
together was built without knowing this file exists, which is the test of
whether the layering held.
"""

from logging import getLogger
from typing import Any

from livekit import rtc
from livekit.agents import Agent, AgentSession, JobContext, RoomInputOptions
from livekit.agents import vad as vad_module

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


def build_session(vad: vad_module.VAD | None = None) -> AgentSession[Any]:
    agent_settings = get_agent_settings()

    session: AgentSession[Any] = AgentSession(
        vad=vad,
        # The VAD is handed to the STT factory too: when a fallback is configured
        # it wraps the two providers in a FallbackAdapter, which needs a VAD to
        # segment any batch member into the streaming interface.
        stt=create_stt(get_stt_settings(), agent_settings.language, vad=vad),
        llm=create_llm(get_llm_settings()),
        tts=create_tts(get_tts_settings(), agent_settings.language),
        # A guest interrupting the agent mid-sentence is normal on a phone call
        # ("no, the Friday one") — allowing it is what makes the exchange feel
        # like a conversation rather than a menu tree.
        allow_interruptions=True,
        # Two tool steps per turn: check availability, then answer. Booking is a
        # separate turn by design, because the guest has to agree in between.
        max_tool_steps=2,
        # Endpointing is the single largest slice of perceived latency, and none
        # of it is provider time. After the guest stops talking the session waits
        # to be sure they are done; the turn-detection model is least sure
        # exactly when a booking utterance ends — on a date or a number
        # ("...for the 24th") — so it sits out the whole max delay. A restaurant
        # booking is short and turn-based, so we cap that wait hard rather than
        # leave the six-second default in place.
        min_endpointing_delay=0.4,
        max_endpointing_delay=2.0,
        # Start drafting the reply (and any tool call) while the final transcript
        # is still settling, instead of after. Overlaps the LLM with the tail of
        # STT, which is free latency back on a sequential cloud pipeline.
        preemptive_generation=True,
    )
    return session


async def start_session(
    session: AgentSession[Any],
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


async def greet(session: AgentSession[Any], agent: BookingAgent, notice: DegradationNotice, room: rtc.Room) -> None:
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
