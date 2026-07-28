from abc import ABC, abstractmethod


class LiveKitGateway(ABC):
    """The port through which this service asks whether LiveKit is reachable.

    A port for one boolean looks like ceremony until you try to test readiness:
    the only implementation opens a real connection to a real server, so without
    this seam `GET /health/ready` can only be covered by standing one up.
    """

    @abstractmethod
    async def ping(self) -> bool:
        """True when the server answered an authenticated call.

        Never raises: a probe that propagates its own failure turns a readiness
        check into a 500, which says "this service is broken" when the honest
        answer is "the thing it depends on is".
        """
