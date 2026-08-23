from fastapi import APIRouter

from ame.api.deps import PageDep, SessionDep
from ame.api.schemas import EventsOut
from ame.api.services import list_events

router = APIRouter(tags=["events"])


@router.get("/events", response_model=EventsOut)
async def get_events(session: SessionDep, page: PageDep) -> dict:
    return await list_events(session, page.limit, page.offset)
