"""The prompt registry.

Cheap tests for the rules that are easy to break by adding one file: prompts are
English, every placeholder is filled by somebody, and asking for a name that is
not there fails loudly rather than sending an empty instruction to the model.
"""

import re

import pytest

from src.app.prompts import _PROMPTS, get_prompt

_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def test_every_prompt_is_english() -> None:
    """The reply language is a placeholder, never a translated file.

    A translated copy forks the prompt, so the next edit lands in one and not
    the other — and Cyrillic costs two to three times the tokens on every call.
    Text the guest hears verbatim lives in `messages/` instead.
    """
    translated = [name for name, prompt in _PROMPTS.items() if _CYRILLIC.search(prompt.text)]

    assert translated == []


def test_an_unknown_prompt_raises_instead_of_returning_nothing() -> None:
    with pytest.raises(KeyError, match="unknown prompt"):
        get_prompt("no_such_prompt")


def test_a_missing_placeholder_is_named() -> None:
    """`str.format` names one field at a time and never says which template."""
    with pytest.raises(KeyError, match="reply_language"):
        get_prompt("persona").render(agent_name="Мила", venue_name="Aurora")


def test_the_identifier_changes_with_the_wording() -> None:
    """What makes a logged answer traceable to the exact text that produced it."""
    prompt = get_prompt("persona")

    assert prompt.id.startswith("persona@")
    assert prompt.id != f"persona@{'0' * 8}"
