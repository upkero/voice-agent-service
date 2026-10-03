"""The HTTP process.

Deliberately thin. It hands a caller a LiveKit join token and reports its own
health; the conversation itself lives in the agent worker (src/entrypoint.py),
which is a separate process because audio work and request handling have
nothing to say to each other and should not share a scaling decision.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.app.api.v1.exception_handlers import register_exception_handlers
from src.app.api.v1.middleware.rate_limit import register_rate_limiting
from src.app.api.v1.middleware.request_id import register_request_id_middleware
from src.app.api.v1.router import api_router
from src.app.api.v1.routers.health import router as health_router
from src.app.bootstrap.container import ApplicationContainer
from src.app.core.logging import setup_logging
from src.app.core.settings.app import get_app_settings
from src.app.core.settings.logging import get_logging_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    container = ApplicationContainer()
    app.state.container = container
    try:
        # The container is lazy; build the graph the requests use now, so a
        # misconfiguration fails the boot instead of the first request.
        _ = container.access_token_service, container.livekit_gateway
        yield
    finally:
        await container.close()


def create_app() -> FastAPI:
    setup_logging(get_logging_settings())

    app = FastAPI(
        title="Voice Agent Service",
        version="0.1.0",
        description="Issues LiveKit join tokens for the restaurant booking voice agent.",
        lifespan=lifespan,
    )

    settings = get_app_settings()

    # Middleware is registered inside-out: Starlette prepends each one, so the
    # LAST registered runs FIRST. The order below produces the runtime chain
    #     CORS -> request_id -> rate_limit -> routes
    # POST /api/v1/token is called from a browser, and the rate limit middleware
    # short-circuits with 429. That response has to travel back out through CORS,
    # or the rejection reaches the frontend as an opaque "Failed to fetch"
    # instead of a status it can read. CORS outermost also answers the OPTIONS
    # preflight before the limiter counts it — the limiter compares the path
    # only, so a preflight would otherwise spend the guest's quota.
    register_rate_limiting(app)
    register_request_id_middleware(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        # Explicit: this API takes no cookie and no browser credential — the
        # caller is anonymous and the token endpoint is rate limited instead.
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID"],
    )

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(api_router)

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=True)
