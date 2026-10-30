"""The opening line, synthesised before anyone calls.

Left to itself the greeting costs the guest 7-10 seconds of silence after joining: a
model writes it, then the configured voice (TTS_PROVIDER) says it. The text is
fixed per language (`messages.greeting_line`), so its audio can be made once when the
worker starts and played from memory at the start of every call.

Per process, in memory, on purpose: nothing to invalidate when the voice or model
changes, and the worker pool is a handful of processes, so the cost is one short
sentence per language per process at startup. If synthesis fails here the call simply
falls back to the live path (model writes it, the voice says it).
"""

import asyncio
import threading
from collections.abc import AsyncIterator
from logging import getLogger
from typing import get_args

from livekit import rtc
from opentelemetry.instrumentation.utils import suppress_instrumentation

from src.app.core.settings.agent import AgentLanguage, AgentSettings
from src.app.core.settings.tts import TTSSettings
from src.app.llm.tts_factory import create_primary_tts
from src.app.messages import greeting_line

logger = getLogger(__name__)

_FRAMES: dict[tuple[str, str], list[rtc.AudioFrame]] = {}


def cached_greeting(language: str, text: str) -> list[rtc.AudioFrame] | None:
    return _FRAMES.get((language, text))


async def replay(frames: list[rtc.AudioFrame]) -> AsyncIterator[rtc.AudioFrame]:
    for frame in frames:
        yield frame


async def _synthesise(settings: TTSSettings, language: str, text: str) -> list[rtc.AudioFrame]:
    # The primary only: the same voice every call gets, and a client that closes cleanly.
    engine = create_primary_tts(settings, language)
    try:
        # `async with`: the stream ends its tts_request span only in aclose, and running
        # out of frames does not call it. Unclosed, the span never left the process and
        # its child landed in Tempo as a trace with no root.
        async with engine.synthesize(text) as stream:
            return [event.frame async for event in stream]
    finally:
        await engine.aclose()


async def _prepare(agent_settings: AgentSettings, tts_settings: TTSSettings, language: str) -> None:
    call = agent_settings.for_language(language)
    text = greeting_line(language, call.display_name, call.venue_name)
    try:
        frames = await _synthesise(tts_settings, language, text)
    except Exception:
        logger.warning("Could not pre-synthesise the %s greeting", language, exc_info=True)
        return
    if frames:
        _FRAMES[(language, text)] = frames
        logger.info("Greeting audio ready", extra={"language": language, "frames": len(frames)})


def prewarm_greetings(agent_settings: AgentSettings, tts_settings: TTSSettings) -> None:
    """Synthesise the greeting for every supported language, concurrently. Blocking."""

    async def run() -> None:
        await asyncio.gather(*(_prepare(agent_settings, tts_settings, lang) for lang in get_args(AgentLanguage)))

    # Not part of any call, so not traced: CollapsingSpanProcessor drops what starts here.
    with suppress_instrumentation():
        asyncio.run(run())


def prewarm_greetings_in_background(agent_settings: AgentSettings, tts_settings: TTSSettings) -> None:
    """Start `prewarm_greetings` on a thread and return at once.

    It must not run inside the worker's `prewarm`: livekit gives process initialisation
    a fixed budget (`initialize_process_timeout` in `src/entrypoint.py`), a cloud voice can
    take a large share of it for two sentences, and a process that overruns is killed and
    respawned in a loop. A call that arrives before the audio is ready just takes the live
    path, so being late costs nothing.
    """
    threading.Thread(
        target=prewarm_greetings, args=(agent_settings, tts_settings), name="greeting-prewarm", daemon=True
    ).start()
