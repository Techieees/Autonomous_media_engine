from fastapi import APIRouter

from ame.api.deps import SessionDep
from ame.ops.notifications import list_notifications, serialize_notification

router = APIRouter(tags=["notifications"])


@router.get("/notifications")
async def get_notifications(session: SessionDep) -> dict:
    rows = await list_notifications(session, limit=50)
    return {"items": [serialize_notification(row) for row in rows]}
