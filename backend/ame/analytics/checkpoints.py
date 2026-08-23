from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ame.contracts.enums import MetricCheckpoint

CHECKPOINT_ORDER: tuple[MetricCheckpoint, ...] = (
    MetricCheckpoint.H1,
    MetricCheckpoint.H6,
    MetricCheckpoint.H24,
    MetricCheckpoint.H72,
    MetricCheckpoint.D7,
    MetricCheckpoint.D30,
)

CHECKPOINT_DELAY: dict[str, timedelta] = {
    MetricCheckpoint.H1.value: timedelta(hours=1),
    MetricCheckpoint.H6.value: timedelta(hours=6),
    MetricCheckpoint.H24.value: timedelta(hours=24),
    MetricCheckpoint.H72.value: timedelta(hours=72),
    MetricCheckpoint.D7.value: timedelta(days=7),
    MetricCheckpoint.D30.value: timedelta(days=30),
}

CHECKPOINT_RANK: dict[str, int] = {
    value: index for index, value in enumerate(CHECKPOINT_ORDER)
}

WINDOW_TO_CHECKPOINT: dict[str, str] = {
    "1h": MetricCheckpoint.H1.value,
    "6h": MetricCheckpoint.H6.value,
    "24h": MetricCheckpoint.H24.value,
    "72h": MetricCheckpoint.H72.value,
    "7d": MetricCheckpoint.D7.value,
    "30d": MetricCheckpoint.D30.value,
    "lifetime": MetricCheckpoint.D30.value,
}


def parse_checkpoint(raw: str | None) -> str:
    if not raw:
        return MetricCheckpoint.H1.value
    text = str(raw).strip().lower()
    aliases = {
        "1": MetricCheckpoint.H1.value,
        "1hour": MetricCheckpoint.H1.value,
        "6": MetricCheckpoint.H6.value,
        "24": MetricCheckpoint.H24.value,
        "72": MetricCheckpoint.H72.value,
        "7": MetricCheckpoint.D7.value,
        "30": MetricCheckpoint.D30.value,
    }
    if text in CHECKPOINT_DELAY:
        return text
    if text in aliases:
        return aliases[text]
    try:
        return MetricCheckpoint(text).value
    except ValueError:
        return MetricCheckpoint.H1.value


def next_checkpoint(current: str) -> str | None:
    current_value = parse_checkpoint(current)
    for index, item in enumerate(CHECKPOINT_ORDER):
        if item.value == current_value and index + 1 < len(CHECKPOINT_ORDER):
            return CHECKPOINT_ORDER[index + 1].value
    return None


def checkpoint_run_after(anchor: datetime, checkpoint: str, *, now: datetime | None = None) -> datetime:
    moment = now or datetime.now(UTC)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    delay = CHECKPOINT_DELAY.get(parse_checkpoint(checkpoint), timedelta(hours=1))
    return max(moment, anchor + delay)


def preferred_snapshot(snapshots: list, *, window: str | None = None):
    if not snapshots:
        return None
    wanted = WINDOW_TO_CHECKPOINT.get((window or "").lower())
    if wanted and wanted != "lifetime":
        match = [item for item in snapshots if item.checkpoint == wanted]
        if match:
            return max(match, key=lambda item: item.created_at)
    return max(
        snapshots,
        key=lambda item: (CHECKPOINT_RANK.get(item.checkpoint, -1), item.created_at),
    )
