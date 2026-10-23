"""Does the name a guest says now belong to the booking made under a name before?

The two names come from speech recognition on two different calls, so they are
rarely byte-identical: Russian inflects a name by the sentence around it
("Андрей" is stored, "Андрея" or "Андрею" is heard), and the recogniser can
spell the same sound two ways. A substring match, which is what ops-core-api
offers, fails on both — so the match is made here, on the day's bookings.

Stdlib only. The idea is to compare stems, not to understand Russian: strip
what a case ending could be, then allow what is left to differ by a letter.
"""

import re
from difflib import SequenceMatcher
from functools import lru_cache

# How alike two stems must be (difflib ratio, 0..1) to count as one name.
# Lower finds more bookings across STT spelling drift ("Кузнецов"/"Кузнецоф") and
# risks reading back the booking of a different person with a similar name.
# Higher is the reverse: a guest who is misheard is told "no such booking".
# 0.85 is the point where one wrong letter is tolerated from seven letters up
# and no further, and where "Иван" stays distinct from "Иванов" (0.80) and
# "Антон" from "Антонов" (0.83): those are a first name and a surname.
NAME_SIMILARITY_THRESHOLD = 0.85

# A trailing "-ом"/"-ем"/"-ым"/"-им" (instrumental), then any run of vowels,
# "й" and "ь": together they cover every case ending of a first name or a
# surname — Андрей/Андрея/Андрею/Андреем, Мария/Марии/Марией, Иванова/Ивановой.
# "-ов"/"-ев" are deliberately not stripped: they belong to the surname.
_INSTRUMENTAL = re.compile(r"[оеыи]м$")
_TRAILING_VOWELS = re.compile(r"[аеёиоуыэюяйь]+$")
_NOT_A_LETTER = re.compile(r"[\W_]+")
_MIN_STEM = 2


def _normalise(name: str) -> str:
    return _NOT_A_LETTER.sub(" ", name.casefold().replace("ё", "е")).strip()


@lru_cache(maxsize=1024)
def _stem(token: str) -> str:
    stem = _TRAILING_VOWELS.sub("", _INSTRUMENTAL.sub("", token))
    # Too short a stem ("Ия", "Ай") would match everything that starts with it.
    return stem if len(stem) >= _MIN_STEM else token


def _same_token(spoken: str, stored: str) -> bool:
    a, b = _stem(spoken), _stem(stored)
    return a == b or SequenceMatcher(None, a, b).ratio() >= NAME_SIMILARITY_THRESHOLD


def names_match(spoken: str, stored: str) -> bool:
    """True when the shorter name is contained in the longer one, word by word.

    Containment, in either direction, because a guest says more or less than
    they did last time: "Андрея Иванова" finds a booking under "Андрей" and
    "Иванову" finds one under "Андрей Иванов". Word order does not matter.

    Decided: a first name never matches a surname that merely starts with it
    ("Иван" does not find "Иванова"), since those are different people; the
    price is that a guest booked as "Иванов" who now says only "Иван" is told
    nothing was found, and is asked to give the full name.
    """
    spoken_tokens = _normalise(spoken).split()
    stored_tokens = _normalise(stored).split()
    if not spoken_tokens or not stored_tokens:
        return False
    shorter, longer = sorted((spoken_tokens, stored_tokens), key=len)
    return all(any(_same_token(token, other) for other in longer) for token in shorter)
