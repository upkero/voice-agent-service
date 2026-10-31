"""Adapter: the BookingGateway port, spoken over HTTP to ops-core-api.

The Adapter pattern earns its place here rather than being decoration. The port
is shaped like a database repository, and this class is what makes an HTTP
service satisfy that shape — mapping the paginated envelope to plain sequences,
translating an error envelope into typed exceptions, and keeping every httpx
type from escaping. ReservationService above it cannot tell the difference, and
the in-memory fakes in the tests are the second implementation that proves it.
"""

from collections.abc import Sequence
from datetime import date, time
from logging import getLogger
from time import perf_counter
from typing import Any
from uuid import UUID

import httpx
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor, RequestInfo
from opentelemetry.trace import Span

from src.app.contracts.booking import BookingDTO, BookingStatus, SlotDTO
from src.app.core.metrics import OPS_CORE_DURATION
from src.app.core.request_id import get_request_id
from src.app.core.resilience import retry_async
from src.app.core.settings.core_api import CoreApiSettings
from src.app.exceptions.booking import (
    BookingError,
    BookingNotFoundError,
    CoreRateLimitedError,
    CoreUnavailableError,
    IdempotencyKeyConsumedError,
    IdempotencyKeyReusedError,
    SlotCapacityError,
    SlotTakenError,
)
from src.app.interfaces.booking_gateway import BookingGateway

logger = getLogger(__name__)

# This agent answers for a restaurant, so it only ever books tables. The other
# resource type ops-core-api knows about (meeting rooms) belongs to a different
# front door.
_RESOURCE_TYPE = "table"

# One date holds a handful of slots, so a single page always covers them. Asking
# for more than exists costs nothing; paging through a conversation would.
# Bookings on a date are not so bounded, so those are paged (list_active_bookings).
_SLOT_PAGE_SIZE = 100

# error_code -> our exception. Switching on the code rather than the status is
# what keeps three different 409s from collapsing into one useless apology.
_ERROR_CODES: dict[str, type[BookingError]] = {
    "slot_unavailable": SlotTakenError,
    "slot_capacity_exceeded": SlotCapacityError,
    "idempotency_key_consumed": IdempotencyKeyConsumedError,
    "idempotency_key_reused": IdempotencyKeyReusedError,
    "entity_not_found": BookingNotFoundError,
}


class _TransientError(Exception):
    """Internal marker for failures worth repeating.

    Private to this module: it exists only to tell the retry decorator what to
    retry, and never reaches a caller — the last one becomes CoreUnavailableError
    or, when the upstream was throttling us, CoreRateLimitedError.

    It carries the response for the same reason: `retry_async` duck-types on
    `.response.headers` to honour `Retry-After`, so an upstream that says "come
    back in 30 seconds" is obeyed instead of being second-guessed by our curve.
    """

    def __init__(self, message: str, response: httpx.Response | None = None) -> None:
        super().__init__(message)
        self.response = response


class CoreApiBookingGateway(BookingGateway):
    def __init__(self, settings: CoreApiSettings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client
        # Bind the retry policy once, from settings, rather than decorating the
        # method at import time with a hard-coded attempt count.
        self._retrying_send = retry_async(
            attempts=settings.max_attempts, retry_on=_TransientError, target="ops_core"
        )(self._send_once)

    async def list_available_slots(self, slot_date: date, party_size: int) -> Sequence[SlotDTO]:
        payload = await self._send(
            "GET",
            "/api/v1/booking-slots",
            params={
                "date": slot_date.isoformat(),
                "resource_type": _RESOURCE_TYPE,
                "limit": _SLOT_PAGE_SIZE,
            },
        )
        slots = [self._to_slot(item) for item in payload.get("items", [])]
        # ops-core-api has no capacity filter, so the port's promise is kept here.
        return [slot for slot in slots if slot.capacity >= party_size]

    async def create_booking(
        self,
        guest_name: str,
        slot_id: UUID,
        party_size: int,
        idempotency_key: str,
    ) -> BookingDTO:
        payload = await self._send(
            "POST",
            "/api/v1/bookings",
            json={"guest_name": guest_name, "slot_id": str(slot_id), "party_size": party_size},
            headers={"Idempotency-Key": idempotency_key},
        )
        return self._to_booking(payload)

    async def list_active_bookings(self, slot_date: date) -> Sequence[BookingDTO]:
        bookings: list[BookingDTO] = []
        while True:
            payload = await self._send(
                "GET",
                "/api/v1/bookings",
                params={
                    "date": slot_date.isoformat(),
                    "status": BookingStatus.ACTIVE.value,
                    "limit": _SLOT_PAGE_SIZE,
                    "offset": len(bookings),
                },
            )
            page = payload.get("items", [])
            bookings.extend(self._to_booking(item) for item in page)
            # An empty page ends the loop even if `total` over-promises, so a
            # misbehaving upstream cannot hold the call open.
            if not page or len(bookings) >= int(payload.get("total", 0)):
                return bookings

    async def cancel_booking(self, booking_id: UUID) -> BookingDTO:
        # No Idempotency-Key: DELETE is idempotent server-side, so a retry after
        # a dropped call returns the same booking and the same 200.
        payload = await self._send("DELETE", f"/api/v1/bookings/{booking_id}")
        return self._to_booking(payload)

    async def close(self) -> None:
        await self._client.aclose()

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send with retries, and let nothing transport-shaped escape.

        The conversion happens here, outside the retry loop: _TransientError is
        the retry decorator's vocabulary, and CoreUnavailableError is the
        domain's. A caller that had to know about both would be coupled to the
        retry mechanism it is meant to be insulated from.
        """
        try:
            return await self._retrying_send(method, path, params=params, json=json, headers=headers)
        except _TransientError as exc:
            logger.warning(
                "ops-core-api unreachable after %d attempts: %s",
                self._settings.max_attempts,
                exc,
                extra={"attempts": self._settings.max_attempts},
            )
            if exc.response is not None and exc.response.status_code == httpx.codes.TOO_MANY_REQUESTS:
                # Still throttled after the last attempt: "busy, try shortly" is a
                # different phrase for the guest from "the diary is down". No
                # Retry-After is passed on, because no HTTP caller ever sees this
                # error: the booking tools turn it into speech.
                raise CoreRateLimitedError() from exc
            raise CoreUnavailableError() from exc

    async def _send_once(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        started, status_class = perf_counter(), "error"
        try:
            response = await self._client.request(method, path, params=params, json=json, headers=headers)
            status_class = f"{response.status_code // 100}xx"
        except httpx.TimeoutException as exc:
            raise _TransientError(f"{method} {path} timed out") from exc
        except httpx.TransportError as exc:
            raise _TransientError(f"{method} {path} failed to connect: {exc}") from exc
        finally:
            # Per attempt, including the ones that never got an answer: a retry
            # storm against a slow ops-core shows up here and nowhere else.
            OPS_CORE_DURATION.labels(method, _route(path), status_class).observe(perf_counter() - started)

        if response.status_code >= 500 or response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            # Server-side and possibly momentary. 429 joins them because it is
            # the one 4xx that says "ask again later" rather than "no": the
            # upstream is fine, it just wants the traffic spread out. Every
            # other 4xx is a rejection, and repeating a rejected request only
            # makes the guest wait for the same answer.
            raise _TransientError(f"{method} {path} returned {response.status_code}", response)
        if response.status_code >= 400:
            raise self._to_exception(response)

        result: dict[str, Any] = response.json()
        return result

    def _to_exception(self, response: httpx.Response) -> BookingError:
        try:
            body = response.json()
            error_code = str(body.get("error_code", ""))
            detail = str(body.get("detail", ""))
        except ValueError:
            error_code, detail = "", response.text[:200]

        exception_type = _ERROR_CODES.get(error_code)
        if exception_type is not None:
            return exception_type(detail or None)

        # An unmapped 4xx means this service and ops-core-api disagree about the
        # contract. That is a bug on our side, so it is logged loudly rather
        # than being folded into "the diary is unavailable".
        logger.error(
            "Unmapped error from ops-core-api: %s %s",
            response.status_code,
            error_code or "<no error_code>",
            extra={"status_code": response.status_code, "error_code": error_code, "detail": detail},
        )
        return BookingError(detail or "The booking service rejected the request.")

    @staticmethod
    def _to_slot(item: dict[str, Any]) -> SlotDTO:
        return SlotDTO(
            id=UUID(item["id"]),
            slot_date=date.fromisoformat(item["slot_date"]),
            slot_time=time.fromisoformat(item["slot_time"]),
            capacity=int(item["capacity"]),
        )

    @staticmethod
    def _to_booking(item: dict[str, Any]) -> BookingDTO:
        return BookingDTO(
            id=UUID(item["id"]),
            guest_name=str(item["guest_name"]),
            slot_id=UUID(item["slot_id"]),
            party_size=int(item["party_size"]),
            # An unknown status is a contract change we have not been told
            # about; failing here beats guessing that it means "active".
            status=BookingStatus(item["status"]),
        )


def _route(path: str) -> str:
    """The path's template: a booking id in a label is a series per booking."""
    return "/api/v1/bookings/{id}" if path.startswith("/api/v1/bookings/") else path


async def _name_span(span: Span, request: RequestInfo) -> None:
    """`ops-core GET /api/v1/bookings`: who was called and for what, not a bare `GET`."""
    span.update_name(f"ops-core {request.method.decode()} {_route(request.url.path)}")


async def _inject_request_id(request: httpx.Request) -> None:
    """Carry the caller's request id into ops-core-api's logs.

    An event hook rather than a static header, because the client is built once
    per process and the id is per call. Without this the two services' logs
    cannot be joined for the same booking, which is precisely the moment anyone
    goes looking.
    """
    if request_id := get_request_id():
        request.headers["X-Request-ID"] = request_id


def create_booking_gateway(
    settings: CoreApiSettings, transport: httpx.AsyncBaseTransport | None = None
) -> CoreApiBookingGateway:
    """Factory: the one place the HTTP client for ops-core-api is built.

    `transport` lets a test run the real client, hooks and instrumentation
    included, against a stub instead of a server.
    """
    client = httpx.AsyncClient(
        base_url=settings.base_url,
        transport=transport,
        timeout=settings.timeout_seconds,
        headers={"X-API-Key": settings.api_key.get_secret_value()},
        event_hooks={"request": [_inject_request_id]},
    )
    # A client span per request and a `traceparent` header, so ops-core-api's
    # server span becomes a child of the booking tool that called it. The
    # X-Request-ID hook stays: logs join on it, and not every log has a trace.
    HTTPXClientInstrumentor().instrument_client(client, request_hook=_name_span)
    return CoreApiBookingGateway(settings, client)
