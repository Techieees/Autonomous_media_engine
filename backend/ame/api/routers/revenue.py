from fastapi import APIRouter

from ame.api.deps import PageDep, SessionDep
from ame.api.schemas import RevenueOut
from ame.api.services import revenue_payload

router = APIRouter(tags=["revenue"])


@router.get("/revenue", response_model=RevenueOut)
async def get_revenue(session: SessionDep, page: PageDep) -> dict:
    return await revenue_payload(session, page.limit, page.offset)
