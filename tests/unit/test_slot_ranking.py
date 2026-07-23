from datetime import time

from src.app.services.booking.ranking import EarliestFirstRanking, NearestTimeRanking
from tests.fakes import make_slot, seeded_slots


def _times(slots) -> list[str]:
    return [slot.slot_time.strftime("%H:%M") for slot in slots]


def test_nearest_time_prefers_the_closest_slot_either_side() -> None:
    """Unsigned distance on purpose.

    Half an hour early beats ninety minutes late, and a rule that only ever
    offers times at or after the request would reliably pick the worse one.
    """
    ranked = NearestTimeRanking().rank(seeded_slots(), time(19, 0))

    assert _times(ranked) == ["19:30", "18:00", "13:30", "12:00"]


def test_nearest_time_breaks_ties_toward_the_earlier_slot() -> None:
    slots = [make_slot(20, 0), make_slot(18, 0)]

    ranked = NearestTimeRanking().rank(slots, time(19, 0))

    assert _times(ranked) == ["18:00", "20:00"]


def test_nearest_time_without_a_preference_is_chronological() -> None:
    ranked = NearestTimeRanking().rank(seeded_slots(), None)

    assert _times(ranked) == ["12:00", "13:30", "18:00", "19:30"]


def test_earliest_first_ignores_the_requested_time() -> None:
    ranked = EarliestFirstRanking().rank(seeded_slots(), time(19, 0))

    assert _times(ranked) == ["12:00", "13:30", "18:00", "19:30"]


def test_both_strategies_satisfy_the_port() -> None:
    """A Strategy with one implementation is an indirection; this one has two."""
    from src.app.interfaces.booking.slot_ranking_strategy import SlotRankingStrategy

    assert isinstance(NearestTimeRanking(), SlotRankingStrategy)
    assert isinstance(EarliestFirstRanking(), SlotRankingStrategy)


def test_ranking_does_not_mutate_its_input() -> None:
    slots = seeded_slots()
    original = list(slots)

    NearestTimeRanking().rank(slots, time(19, 0))

    assert slots == original
