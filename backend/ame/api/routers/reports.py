from fastapi import APIRouter

from ame.api.deps import SessionDep
from ame.ops.reports import generate_daily_report, list_reports, serialize_report

router = APIRouter(tags=["reports"])


@router.get("/reports")
async def get_reports(session: SessionDep) -> dict:
    rows = await list_reports(session)
    today = rows[0] if rows else await generate_daily_report(session)
    await session.commit()
    return {
        "today": serialize_report(today) if today else None,
        "items": [serialize_report(row) for row in rows],
    }


@router.get("/reports/today")
async def get_today_report(session: SessionDep) -> dict:
    report = await generate_daily_report(session)
    await session.commit()
    return serialize_report(report)
