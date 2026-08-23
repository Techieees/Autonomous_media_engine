from typing import Annotated

from fastapi import APIRouter, Query

from ame.api.deps import SessionDep
from ame.api.schemas import AnalyticsOut
from ame.api.services import analytics_payload

router = APIRouter(tags=["analytics"])


@router.get("/analytics", response_model=AnalyticsOut)
async def get_analytics(
    session: SessionDep,
    window: Annotated[str, Query()] = "24h",
) -> dict:
    return await analytics_payload(session, window)
