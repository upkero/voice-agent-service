from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest

from src.app.core.settings.agent import AgentSettings


def test_today_is_the_restaurants_date_not_the_servers() -> None:
    # 22:30 UTC is already the next day in Moscow.
    late_utc = datetime(2026, 10, 23, 22, 30, tzinfo=UTC)
    settings = AgentSettings(_env_file=None, timezone="Europe/Moscow")

    with patch("src.app.core.settings.agent.datetime") as clock:
        clock.now.side_effect = lambda tz: late_utc.astimezone(tz)
        assert settings.today() == date(2026, 10, 24)


def test_an_unknown_timezone_is_rejected() -> None:
    with pytest.raises(ValueError):
        AgentSettings(_env_file=None, timezone="Mars/Olympus")
