from fastapi import APIRouter

from ame.api.deps import PageDep, SessionDep
from ame.api.services import list_content

router = APIRouter(tags=["content"])


@router.get("/content")
async def get_content(session: SessionDep, page: PageDep) -> dict:
    return await list_content(session, page.limit, page.offset)
