import logging
import sys
from typing import Any

import structlog

from ame.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            timestamper,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def bind_job_context(
    *,
    correlation_id: str | None = None,
    workflow_id: str | None = None,
    agent_run_id: str | None = None,
    content_id: str | None = None,
) -> None:
    payload: dict[str, Any] = {}
    if correlation_id:
        payload["correlation_id"] = correlation_id
    if workflow_id:
        payload["workflow_id"] = workflow_id
    if agent_run_id:
        payload["agent_run_id"] = agent_run_id
    if content_id:
        payload["content_id"] = content_id
    structlog.contextvars.bind_contextvars(**payload)


def get_logger(name: str | None = None):
    return structlog.get_logger(name)
