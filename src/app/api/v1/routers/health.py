from logging import getLogger

from fastapi import APIRouter, HTTPException

from src.app.api.v1.dependencies import LiveKitGatewayDep

logger = getLogger(__name__)

router = APIRouter(prefix="/health", tags=["infra"])


@router.get("/live")
async def liveness() -> dict[str, str]:
    """Is the process up. Nothing else — a liveness probe that checks a
    dependency restarts a healthy container because something else broke."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness(livekit: LiveKitGatewayDep) -> dict[str, str]:
    """Can this process do its job.

    Its job is issuing tokens for a LiveKit server, so an unreachable server
    means not ready: a token minted for a room nobody can join is worse than
    an honest 503, because the failure then surfaces inside the browser.
    """
    if not await livekit.ping():
        raise HTTPException(status_code=503, detail="LiveKit server unreachable")
    return {"status": "ok"}
