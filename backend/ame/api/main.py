from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ame.api.errors import register_exception_handlers
from ame.api.routers import api_router
from ame.api.routers.health import router as root_health_router
from ame.api.services import run_startup_seed
from ame.config import get_settings
from ame.db.session import init_database
from ame.observability import configure_logging


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    init_database()
    await run_startup_seed()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="Autonomous Media Engine",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(application)
    application.include_router(root_health_router)
    application.include_router(api_router)
    return application


app = create_app()
