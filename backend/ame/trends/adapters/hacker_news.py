from __future__ import annotations

import asyncio
from datetime import datetime

import httpx

from ame.config import Settings
from ame.contracts.schemas import TrendSignalIn
from ame.observability import get_logger
from ame.trends.adapters.base import TrendAdapter
from ame.trends.http import get_json
from ame.trends.normalize import (
    age_hours_between,
    clamp_text,
    derive_topic,
    engagement_rate,
    utc_from_unix,
    velocity_from_volume,
)

logger = get_logger("ame.trends.hn")

HN_BASE = "https://hacker-news.firebaseio.com/v0"
STORY_LIMIT = 20
SOURCE_AUTHORITY = 0.8
RISK_SCORE = 0.08


class HackerNewsAdapter(TrendAdapter):
    name = "hacker_news"

    def is_configured(self, settings: Settings) -> bool:
        return bool(settings.hacker_news_enabled)

    async def fetch(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        *,
        now: datetime,
    ) -> list[TrendSignalIn]:
        if not settings.hacker_news_enabled:
            return []
        story_ids = await self._collect_story_ids(client)
        if not story_ids:
            return []
        semaphore = asyncio.Semaphore(8)
        loaded = await asyncio.gather(
            *(self._load_item(client, story_id, semaphore) for story_id in story_ids)
        )
        signals: list[TrendSignalIn] = []
        for item in loaded:
            signal = self._normalize(item, now)
            if signal is not None:
                signals.append(signal)
        return signals

    async def _collect_story_ids(self, client: httpx.AsyncClient) -> list[int]:
        seen: set[int] = set()
        ordered: list[int] = []
        for listing in ("topstories", "beststories"):
            payload = await get_json(client, f"{HN_BASE}/{listing}.json")
            if not isinstance(payload, list):
                continue
            for raw_id in payload[:STORY_LIMIT]:
                try:
                    story_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if story_id in seen:
                    continue
                seen.add(story_id)
                ordered.append(story_id)
        return ordered

    async def _load_item(
        self,
        client: httpx.AsyncClient,
        story_id: int,
        semaphore: asyncio.Semaphore,
    ) -> dict | None:
        async with semaphore:
            try:
                payload = await get_json(client, f"{HN_BASE}/item/{story_id}.json")
            except RuntimeError:
                logger.warning("hn_item_failed", story_id=story_id)
                return None
        return payload if isinstance(payload, dict) else None

    def _normalize(self, item: dict | None, now: datetime) -> TrendSignalIn | None:
        if not item:
            return None
        if item.get("dead") or item.get("deleted"):
            return None
        if item.get("type") not in {None, "story"}:
            return None
        title = clamp_text(item.get("title"), 500)
        if not title:
            return None
        external_id = str(item.get("id") or "")
        if not external_id:
            return None
        published_at = utc_from_unix(item.get("time"))
        age_hours = age_hours_between(published_at, now) if published_at else 12.0
        score = item.get("score")
        likes = score if isinstance(score, int) else None
        comments = item.get("descendants") if isinstance(item.get("descendants"), int) else None
        url = item.get("url") or f"https://news.ycombinator.com/item?id={external_id}"
        return TrendSignalIn(
            source=self.name,
            external_id=external_id[:200],
            topic=derive_topic(title),
            title=title,
            url=url,
            published_at=published_at,
            views=None,
            likes=likes,
            comments=comments,
            velocity=velocity_from_volume(likes, age_hours),
            engagement_rate=engagement_rate(likes, comments, None),
            age_hours=age_hours,
            cross_platform_count=1,
            source_authority=SOURCE_AUTHORITY,
            risk_score=RISK_SCORE,
            metadata={
                "by": item.get("by"),
                "hn_type": item.get("type"),
                "listing": "top_best",
            },
        )
