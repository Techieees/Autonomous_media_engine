from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ame.config import Settings, get_settings
from ame.db.session import get_session


class PageParams:
    def __init__(self, limit: int, offset: int) -> None:
        self.limit = limit
        self.offset = offset


def page_params(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
PageDep = Annotated[PageParams, Depends(page_params)]
