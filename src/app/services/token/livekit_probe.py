"""Is the LiveKit server actually there.

Separate from AccessTokenService because signing a token and reaching a server
are different responsibilities with different failure modes: signing works
offline and cannot fail transiently, and a probe is worthless if it cannot.
"""

from logging import getLogger

import aiohttp
from livekit import api

from src.app.core.settings.livekit import LiveKitSettings, get_livekit_settings

logger = getLogger(__name__)

_PROBE_TIMEOUT_SECONDS = 3.0


def to_http_url(url: str) -> str:
    """LiveKit is configured by its signalling URL; its management API is HTTP.

    One setting rather than two, because two would eventually disagree about
    which host the server is on.
    """
    if url.startswith("wss://"):
        return "https://" + url[len("wss://") :]
    if url.startswith("ws://"):
        return "http://" + url[len("ws://") :]
    return url


async def ping_livekit(settings: LiveKitSettings | None = None) -> bool:
    settings = settings or get_livekit_settings()
    # internal_url, not url: the probe runs server-side, where the address that
    # goes into a browser's token (often localhost) does not point at the server.
    client = api.LiveKitAPI(
        url=to_http_url(settings.internal_url or settings.url),
        api_key=settings.api_key,
        api_secret=settings.api_secret.get_secret_value(),
        timeout=aiohttp.ClientTimeout(total=_PROBE_TIMEOUT_SECONDS),
    )
    try:
        # Listing rooms is the cheapest authenticated call there is: it proves
        # the server is up *and* that the key pair is the one it accepts, which
        # a plain TCP check would not.
        await client.room.list_rooms(api.ListRoomsRequest())
        return True
    except Exception as exc:
        logger.warning("LiveKit probe failed: %s", exc, extra={"livekit_url": settings.url})
        return False
    finally:
        await client.aclose()
