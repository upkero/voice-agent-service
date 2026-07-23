"""Factory for the dialogue LLM.

The one place an LLM client is constructed. Callers name a provider in settings
and receive something that satisfies livekit's `llm.LLM`; nothing above this
module imports a provider package, so adding one is a branch here rather than
an edit spread across the agent.

Why the plugin's client rather than the project's own `LLMClient` port, as the
sibling services use: a voice pipeline needs streamed tokens and streamed
tool-call deltas, so the session can start speaking before the sentence is
finished. A `complete(messages) -> str` port cannot express that, and wrapping
one to look like it could would mean re-implementing the streaming protocol
that this plugin already is. The port is the right abstraction for a request /
response service; it is the wrong one for a phone call.
"""

from typing import Any

import httpx
from livekit.agents import llm
from livekit.plugins import openai

from src.app.core.settings.llm import LLMSettings
from src.app.exceptions.llm import LLMConfigurationError


def create_llm(settings: LLMSettings) -> llm.LLM:
    if settings.provider == "openai" and not settings.api_key:
        raise LLMConfigurationError("LLM_API_KEY is required when LLM_PROVIDER='openai'.")
    if settings.provider != "openai" and not settings.base_url:
        raise LLMConfigurationError("LLM_BASE_URL is required for openai_compatible and ollama providers.")

    # Built conditionally: the plugin distinguishes "not given" from None, and
    # passing None for an unset option is not the same as leaving it out.
    kwargs: dict[str, Any] = {
        "model": settings.model,
        # Local gateways such as Ollama ignore the key but the SDK insists on
        # one being present.
        "api_key": settings.api_key or "not-needed",
        "timeout": httpx.Timeout(settings.timeout_seconds),
        "max_retries": settings.max_retries,
        # Ollama and most OpenAI-compatible gateways either reject parallel
        # tool calls or answer them inconsistently. One call per turn is also
        # what this agent wants: checking availability and booking a table in
        # the same breath would book before the guest heard the option.
        "parallel_tool_calls": False,
    }
    if settings.base_url:
        kwargs["base_url"] = settings.base_url
    if settings.temperature is not None:
        kwargs["temperature"] = settings.temperature
    if settings.max_tokens is not None:
        kwargs["max_completion_tokens"] = settings.max_tokens
    if settings.reasoning_effort is not None:
        kwargs["reasoning_effort"] = settings.reasoning_effort

    return openai.LLM(**kwargs)
