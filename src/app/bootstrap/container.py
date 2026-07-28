"""Manual dependency-injection container.

`cached_property` gives lazy singletons without a framework: nothing is built
until something asks for it, and asking twice returns the same object. The HTTP
process therefore never constructs an STT model, and the worker never opens a
token signer — each pays only for what it touches, from one wiring file.
"""

import inspect
from functools import cached_property

from src.app.core.settings.agent import get_agent_settings
from src.app.core.settings.core_api import get_core_api_settings
from src.app.core.settings.livekit import get_livekit_settings
from src.app.gateways.core_api_booking import create_booking_gateway
from src.app.gateways.livekit_probe import LiveKitApiGateway
from src.app.interfaces.booking.slot_ranking_strategy import SlotRankingStrategy
from src.app.interfaces.booking_gateway import BookingGateway
from src.app.interfaces.livekit_gateway import LiveKitGateway
from src.app.services.booking.ranking import NearestTimeRanking
from src.app.services.booking.reservation_service import ReservationService
from src.app.services.dialog.flow import RestaurantBookingFlow
from src.app.services.token.access_token_service import AccessTokenService


class ApplicationContainer:
    @cached_property
    def access_token_service(self) -> AccessTokenService:
        return AccessTokenService(get_livekit_settings())

    @cached_property
    def livekit_gateway(self) -> LiveKitGateway:
        return LiveKitApiGateway(get_livekit_settings())

    @cached_property
    def booking_gateway(self) -> BookingGateway:
        return create_booking_gateway(get_core_api_settings())

    @cached_property
    def slot_ranking(self) -> SlotRankingStrategy:
        # The Strategy is chosen here and nowhere else. Switching the venue to
        # EarliestFirstRanking is this one line; ReservationService never learns
        # that anything changed.
        return NearestTimeRanking()

    @cached_property
    def reservation_service(self) -> ReservationService:
        return ReservationService(self.booking_gateway, self.slot_ranking)

    @cached_property
    def dialog_flow(self) -> RestaurantBookingFlow:
        return RestaurantBookingFlow(get_agent_settings())

    async def close(self) -> None:
        """Close whatever was actually built, once each.

        Duck-typed rather than a registry of closers: a dependency that owns a
        resource already knows how to release it, and a list of what to close is
        one more thing to forget to update.
        """
        closed: set[int] = set()
        for dependency in tuple(self.__dict__.values()):
            if id(dependency) in closed:
                continue
            close = getattr(dependency, "close", None)
            if callable(close):
                result = close()
                if inspect.isawaitable(result):
                    await result
            closed.add(id(dependency))
