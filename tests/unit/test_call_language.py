import pytest

from src.app.core.settings.agent import AgentSettings


@pytest.mark.parametrize("raw", [None, "", "de", "EN"])
def test_an_unusable_language_falls_back_to_the_configured_one(raw: str | None) -> None:
    settings = AgentSettings(_env_file=None, language="ru")

    assert settings.for_language(raw).language == "ru"


def test_the_guests_language_wins_and_the_rest_is_untouched() -> None:
    settings = AgentSettings(_env_file=None, language="ru", name="Мила", venue_name="Aurora")

    call = settings.for_language("en")

    assert (call.language, call.name, call.venue_name) == ("en", "Мила", "Aurora")
    assert settings.language == "ru"  # the process-wide settings are not mutated
