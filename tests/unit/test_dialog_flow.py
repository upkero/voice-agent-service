"""The system prompt's order, and the instructions that must survive any later
editing of the persona."""

from datetime import date

import pytest

from src.app.core.settings.agent import AgentSettings
from src.app.services.dialog.flow import DialogFlow

TODAY = date(2026, 7, 23)


def _flow(language: str = "en") -> DialogFlow:
    return DialogFlow(AgentSettings(language=language), today=lambda: TODAY)


def test_the_sections_are_assembled_in_a_fixed_order() -> None:
    """Persona before rules, rules before trivia.

    Models stop honouring an instruction that has been buried, and "never
    confirm a table the guest did not agree to" is not one to bury.
    """
    prompt = _flow().system_prompt()

    persona = prompt.index("receptionist")
    rules = prompt.index("Rules you must follow")
    context = prompt.index("Today's date")
    speech = prompt.index("speaking out loud")

    assert persona < rules < context < speech


def test_todays_date_is_stated_because_a_model_cannot_know_it() -> None:
    assert "2026-07-23" in _flow().system_prompt()


def test_the_prompt_forbids_reading_identifiers_aloud() -> None:
    prompt = _flow().system_prompt().lower()

    assert "never read out reference codes" in prompt


def test_the_prompt_requires_agreement_before_booking() -> None:
    prompt = _flow().system_prompt()

    assert "wait" in prompt and "confirmed set to true" in prompt


def test_the_prompt_forbids_inventing_availability() -> None:
    assert "until check_availability has told you so" in _flow().system_prompt()


def test_a_booking_from_this_call_is_cancelled_without_a_lookup() -> None:
    assert "Do not look it up again" in _flow().system_prompt()


@pytest.mark.parametrize(("language", "expected"), [("ru", "Russian"), ("en", "English")])
def test_the_persona_states_the_language(language: str, expected: str) -> None:
    assert f"reply only in {expected}" in _flow(language).system_prompt()


@pytest.mark.parametrize(("language", "expected"), [("ru", "Russian"), ("en", "English")])
def test_the_greeting_names_the_reply_language_rather_than_being_translated(language: str, expected: str) -> None:
    """One English instruction with a placeholder, not one file per language:
    a translated copy is a second text to keep in step for an instruction the
    guest never hears."""
    greeting = _flow(language).greeting()

    assert greeting.startswith("Greet the guest")
    assert expected in greeting


def test_the_venue_and_name_come_from_settings() -> None:
    flow = DialogFlow(
        AgentSettings(language="ru", name="Nadia", venue_name="Bell & Anchor"),
        today=lambda: TODAY,
    )

    prompt = flow.system_prompt()
    assert "Nadia" in prompt
    assert "Bell & Anchor" in prompt


def test_an_english_call_uses_the_latin_name_and_says_it_in_the_greeting() -> None:
    flow = DialogFlow(
        AgentSettings(_env_file=None, language="en", name="Мила", name_en="Mila", venue_name="Aurora"),
        today=lambda: TODAY,
    )

    assert "Mila" in flow.system_prompt()
    assert "Мила" not in flow.system_prompt()
    assert flow.greeting_text() == "Hello, Aurora restaurant, this is Mila. How can I help?"


def test_a_russian_call_keeps_the_cyrillic_name() -> None:
    flow = DialogFlow(
        AgentSettings(_env_file=None, language="ru", name="Мила", name_en="Mila", venue_name="Aurora"),
        today=lambda: TODAY,
    )

    assert flow.greeting_text() == "Здравствуйте, ресторан Aurora, меня зовут Мила. Слушаю вас."

