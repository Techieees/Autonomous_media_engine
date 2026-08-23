from fastapi import APIRouter

from ame.api.schemas import HealthOut
from ame.api.services import health_payload

router = APIRouter(tags=["health"])
api_router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
@api_router.get("/health", response_model=HealthOut)
async def health() -> dict:
    return await health_payload()
