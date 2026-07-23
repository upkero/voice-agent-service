"""Concrete slot ranking strategies.

Two implementations, not one, because a Strategy with a single product is just
an indirection. Both are genuinely useful: NearestTime is what a guest asking
for a time expects, EarliestFirst is what a venue trying to turn tables twice
in an evening wants. Swapping them is a one-line change in the container.
"""

from collections.abc import Sequence
from datetime import time

from src.app.contracts.booking import SlotDTO
from src.app.interfaces.booking.slot_ranking_strategy import SlotRankingStrategy


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


class NearestTimeRanking(SlotRankingStrategy):
    """Closest to the requested time first; earliest first when none was given.

    Distance is unsigned on purpose. A guest who asks for 19:00 and is offered
    18:00 has a table half an hour earlier; the same guest offered 20:30 eats
    ninety minutes late. Preferring "not before the requested time" sounds
    tidier and reliably produces the worse suggestion.
    """

    def rank(self, slots: Sequence[SlotDTO], preferred_time: time | None) -> list[SlotDTO]:
        if preferred_time is None:
            return sorted(slots, key=lambda slot: slot.slot_time)
        target = _minutes(preferred_time)
        # Ties broken by the earlier slot, so 18:00 and 20:00 against a 19:00
        # request is a stable, explainable answer rather than dict ordering.
        return sorted(slots, key=lambda slot: (abs(_minutes(slot.slot_time) - target), _minutes(slot.slot_time)))


class EarliestFirstRanking(SlotRankingStrategy):
    """Always the earliest free slot, whatever was asked for."""

    def rank(self, slots: Sequence[SlotDTO], preferred_time: time | None) -> list[SlotDTO]:
        return sorted(slots, key=lambda slot: slot.slot_time)
