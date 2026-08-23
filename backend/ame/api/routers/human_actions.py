from uuid import UUID

from fastapi import APIRouter

from ame.api.deps import PageDep, SessionDep
from ame.api.schemas import HumanActionOut
from ame.api.services import complete_human_action, list_human_actions

router = APIRouter(tags=["human-actions"])


@router.get("/human-actions")
async def get_human_actions(session: SessionDep, page: PageDep) -> dict:
    return await list_human_actions(session, page.limit, page.offset)


@router.post("/human-actions/{action_id}/complete", response_model=HumanActionOut)
async def post_complete_human_action(action_id: UUID, session: SessionDep) -> dict:
    return await complete_human_action(session, action_id)
