from fastapi import APIRouter

from src.app.api.v1.dependencies import AccessTokenServiceDep
from src.app.schemas.token import TokenRequest, TokenResponse

router = APIRouter(prefix="/token", tags=["livekit"])


@router.post("", response_model=TokenResponse)
async def issue_token(body: TokenRequest, service: AccessTokenServiceDep) -> TokenResponse:
    # Schema in, contract out, one service call in between. The router knows
    # nothing about JWTs, grants or LiveKit — moving to a different transport
    # would not touch the signing logic.
    access = service.issue(
        participant_name=body.participant_name, room_name=body.room_name, language=body.language
    )
    return TokenResponse.from_contract(access)
