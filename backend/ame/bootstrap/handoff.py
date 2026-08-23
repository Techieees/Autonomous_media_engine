"""Open the exact official page in the owner browser. Persist that the flow is live."""

from __future__ import annotations

import os
import webbrowser
from datetime import UTC, datetime
from typing import Any

from ame.config import get_settings
from ame.observability import get_logger

logger = get_logger("ame.bootstrap.handoff")


def browser_open_allowed() -> bool:
    settings = get_settings()
    if not settings.bootstrap_open_browser:
        return False
    if settings.app_env == "test":
        return False
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    if os.getenv("AME_ACCEPTANCE_DRIVE_JOBS") == "1":
        return False
    return True


def launch_official_page(url: str, *, purpose: str, persist: dict[str, Any] | None = None) -> dict[str, Any]:
    opened = False
    allowed = browser_open_allowed()
    if allowed:
        try:
            opened = bool(webbrowser.open(url, new=2))
        except Exception:
            logger.warning("browser_handoff_failed", url=url, purpose=purpose)
            opened = False
    record = {
        "url": url,
        "purpose": purpose,
        "opened": opened,
        "attempted": allowed,
        "launched_at": datetime.now(UTC).isoformat(),
    }
    if persist is not None:
        persist.update(record)
    logger.info("browser_handoff", url=url, purpose=purpose, opened=opened, attempted=allowed)
    return record
