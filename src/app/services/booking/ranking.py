from collections.abc import Sequence
from datetime import time

from src.app.contracts.booking import SlotDTO


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def rank_slots(slots: Sequence[SlotDTO], preferred_time: time | None) -> list[SlotDTO]:
    """Closest to the requested time first; earliest first when none was given.

    Distance is unsigned on purpose. A guest who asks for 19:00 and is offered
    18:00 has a table half an hour earlier; the same guest offered 20:30 eats
    ninety minutes late. Preferring "not before the requested time" sounds
    tidier and reliably produces the worse suggestion.
    """
    if preferred_time is None:
        return sorted(slots, key=lambda slot: slot.slot_time)
    target = _minutes(preferred_time)
    # Ties broken by the earlier slot, so 18:00 and 20:00 against a 19:00
    # request is a stable, explainable answer rather than dict ordering.
    return sorted(slots, key=lambda slot: (abs(_minutes(slot.slot_time) - target), _minutes(slot.slot_time)))
