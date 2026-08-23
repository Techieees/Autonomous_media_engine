from __future__ import annotations

import hashlib
import random
from typing import Any
from uuid import UUID

from ame.analytics.checkpoints import CHECKPOINT_DELAY, parse_checkpoint

_SCALE = {
    "1h": 0.10,
    "6h": 0.28,
    "24h": 0.58,
    "72h": 0.78,
    "7d": 0.92,
    "30d": 1.00,
}

SYNTHETIC_NOTE = (
    "Deterministic dry-run metrics derived from a hash of the publication id. "
    "Not actual platform reach, engagement, or revenue."
)


def _rng(publication_id: UUID) -> random.Random:
    digest = hashlib.sha256(f"ame-sim-metrics:{publication_id}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def generate_synthetic_metrics(publication_id: UUID, checkpoint: str) -> dict[str, Any]:
    """Labeled synthetic metrics. Never present as actual platform data."""
    checkpoint = parse_checkpoint(checkpoint)
    rng = _rng(publication_id)
    scale = _SCALE.get(checkpoint, 0.1)
    base_views = 24 + rng.randint(0, 360)
    views = max(0, int(base_views * scale))
    like_rate = rng.uniform(0.025, 0.075)
    comment_rate = rng.uniform(0.002, 0.012)
    share_rate = rng.uniform(0.002, 0.018)
    likes = max(0, int(views * like_rate))
    comments = max(0, int(views * comment_rate))
    shares = max(0, int(views * share_rate))
    completion = round(rng.uniform(0.28, 0.68), 4)
    avg_watch = rng.uniform(9.0, 26.0)
    watch_time_seconds = round(views * avg_watch * completion, 2)
    followers_gained = max(0, int(views * rng.uniform(0.0, 0.012)))
    return {
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "watch_time_seconds": watch_time_seconds,
        "completion_rate": completion,
        "followers_gained": followers_gained,
        "simulation": True,
        "raw": {
            "source": "synthetic",
            "labeled": "simulation",
            "not_actual": True,
            "metrics_available": True,
            "checkpoint": checkpoint,
            "hash_seed": "sha256:ame-sim-metrics:{publication_id}",
            "checkpoint_delay_seconds": int(CHECKPOINT_DELAY[checkpoint].total_seconds()),
            "note": SYNTHETIC_NOTE,
        },
    }
