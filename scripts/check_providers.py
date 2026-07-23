"""Prove the configured speech and dialogue providers actually work.

Run this once after putting a real key in .env, before trying a live call. It
exercises the real client classes this service uses — not the raw SDK — so it
catches the failures a live call would otherwise surface as silence:

  * TTS  — synthesises a sentence and checks audio really comes back.
  * STT  — feeds that audio straight back and checks it transcribes.
  * LLM  — sends the actual tool schemas and checks the model calls a tool.

The STT step reuses the TTS output, so a green run means the whole audio
round-trip works end to end through both adapters, with no microphone involved.

    uv run python -m scripts.check_providers

Needs the provider keys in .env (or the environment). Exits non-zero on any
failure, so it doubles as a pre-flight check in a script.
"""

import asyncio
import sys

from livekit import rtc

from src.app.core.settings.agent import get_agent_settings
from src.app.core.settings.llm import get_llm_settings
from src.app.core.settings.stt import get_stt_settings
from src.app.core.settings.tts import get_tts_settings
from src.app.llm.stt_factory import create_stt
from src.app.llm.tts_factory import create_tts

_GREEN, _RED, _DIM, _OFF = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def _ok(msg: str) -> None:
    print(f"{_GREEN}  PASS{_OFF} {msg}")


def _fail(msg: str) -> None:
    print(f"{_RED}  FAIL{_OFF} {msg}")


async def check_tts() -> bytes:
    """Synthesise a sentence; return the raw PCM so STT can reuse it."""
    settings = get_tts_settings()
    language = get_agent_settings().language
    sentence = "У вас есть свободный столик на четверых?" if language == "ru" else "Do you have a table for four?"
    print(f"{_DIM}TTS  {settings.provider} / {settings.model}{_OFF}")

    tts = create_tts(settings, language)
    stream = tts.synthesize(sentence)
    audio = bytearray()
    async for event in stream:
        audio.extend(event.frame.data.tobytes())
    await stream.aclose()
    if hasattr(tts, "aclose"):
        await tts.aclose()

    seconds = len(audio) / (tts.sample_rate * 2)  # 16-bit mono
    if len(audio) == 0:
        _fail("TTS returned no audio")
        raise SystemExit(1)
    _ok(f'spoke "{sentence}" -> {len(audio):,} bytes (~{seconds:.1f}s at {tts.sample_rate} Hz)')
    return bytes(audio)


async def check_stt(pcm: bytes) -> None:
    """Feed the TTS audio back in and confirm it transcribes to words."""
    settings = get_stt_settings()
    language = get_agent_settings().language
    print(f"{_DIM}STT  {settings.provider} / {settings.model}{_OFF}")

    sample_rate = get_tts_settings().sample_rate
    frame = rtc.AudioFrame(
        data=pcm,
        sample_rate=sample_rate,
        num_channels=1,
        samples_per_channel=len(pcm) // 2,
    )
    stt = create_stt(settings, language)
    event = await stt.recognize([frame], language=language)
    if hasattr(stt, "aclose"):
        await stt.aclose()

    text = event.alternatives[0].text if event.alternatives else ""
    if not text.strip():
        _fail("STT transcribed the audio to nothing")
        raise SystemExit(1)
    _ok(f'heard back: "{text.strip()}"')


async def check_llm_tool_call() -> None:
    """Send the real tool schemas and confirm the model calls one.

    Uses the raw OpenAI SDK against the same settings because that is the exact
    wire request the pipeline makes; the point is to prove the schemas are
    accepted and the model books rather than chats.
    """
    from openai import AsyncOpenAI

    from src.app.services.dialog.tools import TOOL_SCHEMAS

    settings = get_llm_settings()
    print(f"{_DIM}LLM  {settings.provider} / {settings.model}{_OFF}")

    client = AsyncOpenAI(api_key=settings.api_key or "x", base_url=settings.base_url, timeout=30.0)
    tools = [{"type": "function", "function": schema} for schema in TOOL_SCHEMAS]
    completion = await client.chat.completions.create(
        model=settings.model,
        messages=[
            {"role": "system", "content": "You book restaurant tables. Use the tools; today is 2026-07-23."},
            {"role": "user", "content": "A table for four tomorrow at seven in the evening, please."},
        ],
        tools=tools,
        temperature=0.0,
    )
    await client.close()

    calls = completion.choices[0].message.tool_calls or []
    if not calls:
        _fail(f"model answered without calling a tool: {completion.choices[0].message.content!r}")
        raise SystemExit(1)
    names = ", ".join(call.function.name for call in calls)
    _ok(f"model called: {names}  args={calls[0].function.arguments}")


async def main() -> None:
    print("Checking the configured providers end to end...\n")
    try:
        pcm = await check_tts()
        await check_stt(pcm)
        await check_llm_tool_call()
    except SystemExit:
        print(f"\n{_RED}One or more providers failed. Fix the key/model above and re-run.{_OFF}")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 - a smoke test reports, it does not raise
        print(f"\n{_RED}  ERROR{_OFF} {type(exc).__name__}: {exc}")
        sys.exit(1)
    print(f"\n{_GREEN}All three providers work. A live call should too.{_OFF}")


if __name__ == "__main__":
    asyncio.run(main())
