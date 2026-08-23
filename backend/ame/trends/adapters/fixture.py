from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ame.contracts.schemas import TrendSignalIn
from ame.observability import get_logger
from ame.trends.normalize import finalize_signal

logger = get_logger("ame.trends.fixture")


def fixture_file() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "fixtures" / "trends" / "sample.json"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("fixtures/trends/sample.json not found")


def load_fixture_signals(now: datetime) -> list[TrendSignalIn]:
    path = fixture_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise RuntimeError("trend fixture must be a JSON list")
    signals: list[TrendSignalIn] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            signal = TrendSignalIn.model_validate(_with_fixture_metadata(row))
        except ValidationError:
            logger.warning("fixture_row_invalid")
            continue
        signals.append(finalize_signal(signal, now))
    return signals


def _with_fixture_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(row.get("metadata") or {})
    metadata.setdefault("fixture", True)
    metadata["fallback"] = True
    copied = dict(row)
    copied["metadata"] = metadata
    return copied
