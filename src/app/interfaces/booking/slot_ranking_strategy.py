from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import time

from src.app.contracts.booking import SlotDTO


class SlotRankingStrategy(ABC):
    """Strategy: how candidate slots are ordered before being offered.

    A guest asking for "19:00" when the diary holds 18:00 and 19:30 is the
    normal case, not the exception, so the ordering rule is a real decision —
    and different venues want different answers. A restaurant filling its late
    sitting ranks by nearest time; one clearing an early service ranks by
    earliest. Making it an injected object rather than a sort key buried in the
    service means adding a rule never edits ReservationService.
    """

    @abstractmethod
    def rank(self, slots: Sequence[SlotDTO], preferred_time: time | None) -> list[SlotDTO]:
        """Return the slots in the order they should be offered."""
