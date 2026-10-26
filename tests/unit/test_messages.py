"""Copy rules for the lines the guest receives word for word."""

from collections.abc import Iterator

import pytest

from src.app.messages import DEGRADATION_MESSAGES, ERROR_PHRASES, FILLERS, GREETINGS


def _guest_lines() -> Iterator[str]:
    for table in (ERROR_PHRASES, DEGRADATION_MESSAGES):
        for lines in table.values():
            yield from lines.values()
    yield from GREETINGS.values()
    for per_tool in FILLERS.values():
        for variants in per_tool.values():
            yield from variants


@pytest.mark.parametrize("line", list(_guest_lines()))
def test_no_long_dash_in_a_guest_facing_line(line: str) -> None:
    """The degradation notices are shown as text on the site, so the copy rule applies."""
    assert "\u2014" not in line and "\u2013" not in line, line


@pytest.mark.parametrize("language", ["ru", "en"])
def test_no_line_promises_a_transfer(language: str) -> None:
    """There is no transfer tool: Mila can only send the guest to the restaurant's own line."""
    for text in ERROR_PHRASES[language].values():
        assert not any(word in text.lower() for word in ("put you through", "manager", "соединю")), text
