from __future__ import annotations

import asyncio
from datetime import datetime

import feedparser
import httpx

from ame.config import Settings
from ame.contracts.schemas import TrendSignalIn
from ame.observability import get_logger
from ame.trends.adapters.base import TrendAdapter
from ame.trends.http import get_response
from ame.trends.normalize import (
    age_hours_between,
    clamp_text,
    derive_topic,
    host_of,
    stable_external_id,
    utc_from_struct,
)

logger = get_logger("ame.trends.rss")

ENTRY_LIMIT_PER_FEED = 15
SOURCE_AUTHORITY = 0.65
RISK_SCORE = 0.1


class RssAdapter(TrendAdapter):
    name = "rss"

    def is_configured(self, settings: Settings) -> bool:
        return bool(settings.rss_feed_list)

    async def fetch(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        *,
        now: datetime,
    ) -> list[TrendSignalIn]:
        feeds = settings.rss_feed_list
        results = await asyncio.gather(
            *(self._fetch_feed(client, url, now) for url in feeds),
            return_exceptions=True,
        )
        signals: list[TrendSignalIn] = []
        failures = 0
        for url, result in zip(feeds, results, strict=True):
            if isinstance(result, BaseException):
                failures += 1
                logger.warning("rss_feed_failed", host=host_of(url))
                continue
            signals.extend(result)
        if feeds and failures == len(feeds):
            raise RuntimeError("all rss feeds failed")
        return signals

    async def _fetch_feed(
        self,
        client: httpx.AsyncClient,
        url: str,
        now: datetime,
    ) -> list[TrendSignalIn]:
        response = await get_response(
            client,
            url,
            headers={
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"
            },
        )
        parsed = await asyncio.to_thread(feedparser.parse, response.content)
        entries = list(getattr(parsed, "entries", []) or [])[:ENTRY_LIMIT_PER_FEED]
        signals: list[TrendSignalIn] = []
        for entry in entries:
            signal = self._normalize(entry, url, now)
            if signal is not None:
                signals.append(signal)
        return signals

    def _normalize(self, entry: object, feed_url: str, now: datetime) -> TrendSignalIn | None:
        title = clamp_text(getattr(entry, "title", None), 500)
        if not title:
            return None
        link = clamp_text(getattr(entry, "link", None), 2000) or None
        raw_id = clamp_text(getattr(entry, "id", None) or link or title, 2000)
        try:
            external_id = stable_external_id(raw_id)
        except ValueError:
            return None
        published_at = utc_from_struct(
            getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
        )
        age_hours = age_hours_between(published_at, now) if published_at else 12.0
        return TrendSignalIn(
            source=self.name,
            external_id=external_id,
            topic=derive_topic(title),
            title=title,
            url=link,
            published_at=published_at,
            views=None,
            likes=None,
            comments=None,
            velocity=1.0 / max(age_hours, 1.0),
            engagement_rate=0.0,
            age_hours=age_hours,
            cross_platform_count=1,
            source_authority=SOURCE_AUTHORITY,
            risk_score=RISK_SCORE,
            metadata={"feed_host": host_of(feed_url)},
        )
