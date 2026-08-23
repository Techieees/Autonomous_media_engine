from fastapi import APIRouter

from ame.api.deps import SessionDep
from ame.api.schemas import BootstrapOut
from ame.api.services import bootstrap_payload

router = APIRouter(tags=["bootstrap"])


@router.get("/bootstrap", response_model=BootstrapOut)
async def get_bootstrap(session: SessionDep) -> dict:
    return await bootstrap_payload(session)
