from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ame.api.deps import SessionDep
from ame.api.oauth import callback_oauth, start_oauth
from ame.contracts.enums import Platform

router = APIRouter(tags=["oauth"])


@router.get("/oauth/youtube/start", response_model=None)
async def youtube_start(session: SessionDep) -> JSONResponse | RedirectResponse:
    return await start_oauth(Platform.YOUTUBE.value, session)


@router.get("/oauth/youtube/callback", response_model=None)
async def youtube_callback(
    request: Request,
    session: SessionDep,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> JSONResponse | RedirectResponse:
    return await callback_oauth(
        Platform.YOUTUBE.value,
        request,
        session,
        code=code,
        state=state,
        error=error,
    )


@router.get("/oauth/instagram/start", response_model=None)
async def instagram_start(session: SessionDep) -> JSONResponse | RedirectResponse:
    return await start_oauth(Platform.INSTAGRAM.value, session)


@router.get("/oauth/instagram/callback", response_model=None)
async def instagram_callback(
    request: Request,
    session: SessionDep,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> JSONResponse | RedirectResponse:
    return await callback_oauth(
        Platform.INSTAGRAM.value,
        request,
        session,
        code=code,
        state=state,
        error=error,
    )


@router.get("/oauth/tiktok/start", response_model=None)
async def tiktok_start(session: SessionDep) -> JSONResponse | RedirectResponse:
    return await start_oauth(Platform.TIKTOK.value, session)


@router.get("/oauth/tiktok/callback", response_model=None)
async def tiktok_callback(
    request: Request,
    session: SessionDep,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> JSONResponse | RedirectResponse:
    return await callback_oauth(
        Platform.TIKTOK.value,
        request,
        session,
        code=code,
        state=state,
        error=error,
    )
