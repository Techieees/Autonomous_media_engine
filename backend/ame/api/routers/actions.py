from fastapi import APIRouter

from ame.api.deps import SessionDep
from ame.api.schemas import RunCycleOut
from ame.api.services import run_cycle

router = APIRouter(tags=["actions"])


@router.post("/actions/run-cycle", response_model=RunCycleOut)
async def post_run_cycle(session: SessionDep) -> dict:
    return await run_cycle(session)
