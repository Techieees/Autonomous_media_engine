"""Owner-local time helpers. No hard-coded timezone besides the configured default."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ame.config import get_settings


def owner_zone() -> ZoneInfo:
    name = (get_settings().owner_timezone or "Europe/Dublin").strip() or "Europe/Dublin"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Europe/Dublin")


def now_utc() -> datetime:
    return datetime.now(UTC)


def owner_now(moment: datetime | None = None) -> datetime:
    current = moment or now_utc()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(owner_zone())


def owner_local_date(moment: datetime | None = None) -> str:
    return owner_now(moment).date().isoformat()


def owner_day_bounds(moment: datetime | None = None) -> tuple[datetime, datetime]:
    local = owner_now(moment)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def near_owner_day_end(moment: datetime | None = None, *, minutes: int = 20) -> bool:
    local = owner_now(moment)
    remaining = (
        local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1) - local
    )
    return remaining <= timedelta(minutes=minutes)


def scheduler_fast() -> bool:
    settings = get_settings()
    return bool(settings.scheduler_fast)


def autonomous_enabled() -> bool:
    return bool(get_settings().autonomous_mode)
