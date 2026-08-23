from __future__ import annotations

from datetime import datetime

import httpx

from ame.config import Settings
from ame.contracts.schemas import TrendSignalIn
from ame.trends.adapters.base import TrendAdapter
from ame.trends.http import get_json
from ame.trends.normalize import (
    age_hours_between,
    clamp_text,
    derive_topic,
    engagement_rate,
    optional_int,
    utc_from_isoformat,
    velocity_from_volume,
)

YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
MAX_RESULTS = 15
SOURCE_AUTHORITY = 0.7
RISK_SCORE = 0.12


class YouTubeAdapter(TrendAdapter):
    name = "youtube"

    def is_configured(self, settings: Settings) -> bool:
        return bool(settings.youtube_data_api_key.strip())

    async def fetch(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        *,
        now: datetime,
    ) -> list[TrendSignalIn]:
        if not settings.youtube_data_api_key.strip():
            return []
        payload = await get_json(
            client,
            YOUTUBE_VIDEOS_URL,
            params={
                "part": "snippet,statistics",
                "chart": "mostPopular",
                "maxResults": MAX_RESULTS,
                "regionCode": "US",
                "key": settings.youtube_data_api_key,
            },
        )
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []
        signals: list[TrendSignalIn] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            signal = self._normalize(item, now)
            if signal is not None:
                signals.append(signal)
        return signals

    def _normalize(self, item: dict, now: datetime) -> TrendSignalIn | None:
        video_id = clamp_text(item.get("id"), 200)
        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
        title = clamp_text(snippet.get("title"), 500)
        if not video_id or not title:
            return None
        published_at = utc_from_isoformat(snippet.get("publishedAt"))
        age_hours = age_hours_between(published_at, now) if published_at else 12.0
        views = optional_int(stats.get("viewCount"))
        likes = optional_int(stats.get("likeCount"))
        comments = optional_int(stats.get("commentCount"))
        return TrendSignalIn(
            source=self.name,
            external_id=video_id,
            topic=derive_topic(title),
            title=title,
            url=f"https://www.youtube.com/watch?v={video_id}",
            published_at=published_at,
            views=views,
            likes=likes,
            comments=comments,
            velocity=velocity_from_volume(views, age_hours),
            engagement_rate=engagement_rate(likes, comments, views),
            age_hours=age_hours,
            cross_platform_count=1,
            source_authority=SOURCE_AUTHORITY,
            risk_score=RISK_SCORE,
            metadata={
                "channel_title": snippet.get("channelTitle"),
                "channel_id": snippet.get("channelId"),
            },
        )
