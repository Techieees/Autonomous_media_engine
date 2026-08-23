from fastapi import APIRouter

from ame.api.deps import PageDep, SessionDep
from ame.api.schemas import StrategyOut
from ame.api.services import list_strategy

router = APIRouter(tags=["strategy"])


@router.get("/strategy", response_model=StrategyOut)
async def get_strategy(session: SessionDep, page: PageDep) -> dict:
    return await list_strategy(session, page.limit, page.offset)
