"""The name matcher, on the pairs that actually break a substring match.

Each pair is (what the guest says now, what is stored from the earlier call).
"""

import pytest

from src.app.services.booking.name_match import names_match


@pytest.mark.parametrize(
    ("spoken", "stored"),
    [
        # Russian case endings: one name, whatever the sentence around it requires.
        ("Андрея", "Андрей"),
        ("Андрею", "Андрей"),
        ("Андреем", "Андрей"),
        ("Андрей", "Андрея"),
        ("Иванову", "Иванова"),
        ("Иванова", "Иванов"),
        ("Ивановой", "Иванов"),
        ("Алексея", "Алексей"),
        ("Марии", "Мария"),
        ("Марию", "Мария"),
        # ё/е, case and whitespace.
        ("Артём", "Артем"),
        ("артем", "АРТЁМ"),
        ("  Андрей   Иванов ", "андрей иванов"),
        ("Анна-Мария", "Анна Мария"),
        # Small STT spelling drift.
        ("Кузнецоф", "Кузнецов"),
        ("Катерина", "Екатерина"),
        ("Petrova", "Petrov"),
        # More or fewer words than last time, in any order.
        ("Андрей", "Андрей Иванов"),
        ("Иванова", "Андрей Иванов"),
        ("Андрея Иванова", "Андрей"),
        ("Иванов Андрей", "Андрей Иванов"),
    ],
)
def test_the_same_person_matches(spoken: str, stored: str) -> None:
    assert names_match(spoken, stored)


@pytest.mark.parametrize(
    ("spoken", "stored"),
    [
        ("Андрей", "Алексей"),
        ("Андрей Иванов", "Андрей Петров"),
        ("Мария", "Марина"),
        ("Сергей", "Сергеев"),
        ("Анна", "Инна"),
        # Decided: a first name is not a surname that merely begins with it.
        ("Иван", "Иванова"),
        ("Иван", "Иванов"),
        ("Антон", "Антонов"),
        ("Иванов", "Иван"),
        # Nothing said, nothing to match.
        ("", "Андрей"),
        ("   ", "Андрей"),
        ("Андрей", ""),
    ],
)
def test_different_people_do_not_match(spoken: str, stored: str) -> None:
    assert not names_match(spoken, stored)
