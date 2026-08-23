from fastapi import APIRouter

from ame.api.deps import PageDep, SessionDep
from ame.api.services import list_trends

router = APIRouter(tags=["trends"])


@router.get("/trends")
async def get_trends(session: SessionDep, page: PageDep) -> dict:
    return await list_trends(session, page.limit, page.offset)
