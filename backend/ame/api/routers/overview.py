from fastapi import APIRouter

from ame.api.deps import SessionDep
from ame.api.schemas import OverviewOut
from ame.api.services import overview_metrics

router = APIRouter(tags=["overview"])


@router.get("/overview", response_model=OverviewOut)
async def get_overview(session: SessionDep) -> dict:
    return await overview_metrics(session)
