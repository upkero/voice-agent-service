from fastapi import APIRouter

from src.app.api.v1.routers.token import router as token_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(token_router)
