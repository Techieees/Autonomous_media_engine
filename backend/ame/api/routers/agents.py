from fastapi import APIRouter

from ame.api.deps import PageDep, SessionDep
from ame.api.schemas import AgentsOut
from ame.api.services import list_agents

router = APIRouter(tags=["agents"])


@router.get("/agents", response_model=AgentsOut)
async def get_agents(session: SessionDep, page: PageDep) -> dict:
    return await list_agents(session, page.limit, page.offset)
