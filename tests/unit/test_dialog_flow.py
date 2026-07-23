"""The dialogue skeleton and what it guarantees.

The Template Method is here to protect an order, so the tests are about order
and about the instructions that must survive any later editing of the persona.
"""

from datetime import date

import pytest

from src.app.core.settings.agent import AgentSettings
from src.app.services.dialog.flow import BaseDialogFlow, RestaurantBookingFlow

TODAY = date(2026, 7, 23)


def _flow(language: str = "en") -> RestaurantBookingFlow:
    return RestaurantBookingFlow(AgentSettings(language=language), today=lambda: TODAY)


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
def test_the_persona_states_the_language(language, expected) -> None:
    assert expected in _flow(language).persona()


def test_the_greeting_follows_the_configured_language() -> None:
    assert "Поздоровайся" in _flow("ru").greeting()
    assert "Greet the guest" in _flow("en").greeting()


def test_the_venue_and_name_come_from_settings() -> None:
    flow = RestaurantBookingFlow(
        AgentSettings(language="en", name="Nadia", venue_name="Bell & Anchor"),
        today=lambda: TODAY,
    )

    prompt = flow.system_prompt()
    assert "Nadia" in prompt
    assert "Bell & Anchor" in prompt


def test_a_subclass_can_replace_a_step_without_reordering_the_skeleton() -> None:
    """The point of the pattern: steps are open, the sequence is not."""

    class TerseFlow(RestaurantBookingFlow):
        def persona(self) -> str:
            return "receptionist, briefly"

    prompt = TerseFlow(AgentSettings(language="en"), today=lambda: TODAY).system_prompt()

    assert prompt.startswith("receptionist, briefly")
    assert prompt.index("Rules you must follow") < prompt.index("Today's date")


def test_the_base_flow_cannot_be_used_without_filling_in_its_steps() -> None:
    with pytest.raises(TypeError):
        BaseDialogFlow(AgentSettings())  # type: ignore[abstract]
