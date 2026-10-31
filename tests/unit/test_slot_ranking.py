from collections.abc import Sequence
from datetime import time

from src.app.contracts.booking import SlotDTO
from src.app.services.booking.ranking import rank_slots
from tests.fakes import make_slot, sample_slots


def _times(slots: Sequence[SlotDTO]) -> list[str]:
    return [slot.slot_time.strftime("%H:%M") for slot in slots]


def test_nearest_time_prefers_the_closest_slot_either_side() -> None:
    """Unsigned distance on purpose.

    Half an hour early beats ninety minutes late, and a rule that only ever
    offers times at or after the request would reliably pick the worse one.
    """
    ranked = rank_slots(sample_slots(), time(19, 0))

    assert _times(ranked) == ["19:30", "18:00", "13:30", "12:00"]


def test_nearest_time_breaks_ties_toward_the_earlier_slot() -> None:
    slots = [make_slot(20, 0), make_slot(18, 0)]

    ranked = rank_slots(slots, time(19, 0))

    assert _times(ranked) == ["18:00", "20:00"]


def test_nearest_time_without_a_preference_is_chronological() -> None:
    ranked = rank_slots(sample_slots(), None)

    assert _times(ranked) == ["12:00", "13:30", "18:00", "19:30"]


def test_ranking_does_not_mutate_its_input() -> None:
    slots = sample_slots()
    original = list(slots)

    rank_slots(slots, time(19, 0))

    assert slots == original
