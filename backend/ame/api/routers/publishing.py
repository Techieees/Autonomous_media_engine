from fastapi import APIRouter

from ame.api.deps import PageDep, SessionDep
from ame.api.schemas import PublishingOut
from ame.api.services import publishing_payload

router = APIRouter(tags=["publishing"])


@router.get("/publishing", response_model=PublishingOut)
async def get_publishing(session: SessionDep, page: PageDep) -> dict:
    return await publishing_payload(session, page.limit, page.offset)
