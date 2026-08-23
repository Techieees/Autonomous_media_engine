from __future__ import annotations

from datetime import datetime

import httpx

from ame.config import Settings
from ame.contracts.schemas import TrendSignalIn
from ame.trends.adapters.base import TrendAdapter
from ame.trends.http import get_json, post_form
from ame.trends.normalize import (
    age_hours_between,
    clamp_text,
    derive_topic,
    engagement_rate,
    optional_int,
    utc_from_unix,
    velocity_from_volume,
)

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
POPULAR_URL = "https://oauth.reddit.com/r/popular/hot"
SOURCE_AUTHORITY = 0.55
RISK_SCORE = 0.18
LIMIT = 25


class RedditAdapter(TrendAdapter):
    name = "reddit"

    def is_configured(self, settings: Settings) -> bool:
        return bool(settings.reddit_client_id.strip() and settings.reddit_client_secret.strip())

    async def fetch(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        *,
        now: datetime,
    ) -> list[TrendSignalIn]:
        if not settings.reddit_client_id.strip() or not settings.reddit_client_secret.strip():
            return []
        token = await self._access_token(client, settings)
        payload = await get_json(
            client,
            POPULAR_URL,
            params={"limit": LIMIT, "raw_json": 1},
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": settings.reddit_user_agent,
            },
        )
        children = (
            payload.get("data", {}).get("children")
            if isinstance(payload, dict)
            else None
        )
        if not isinstance(children, list):
            return []
        signals: list[TrendSignalIn] = []
        for child in children:
            if not isinstance(child, dict):
                continue
            data = child.get("data")
            if not isinstance(data, dict):
                continue
            signal = self._normalize(data, now)
            if signal is not None:
                signals.append(signal)
        return signals

    async def _access_token(self, client: httpx.AsyncClient, settings: Settings) -> str:
        payload = await post_form(
            client,
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(settings.reddit_client_id, settings.reddit_client_secret),
            headers={"User-Agent": settings.reddit_user_agent},
        )
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not token or not isinstance(token, str):
            raise RuntimeError("reddit token response missing access_token")
        return token

    def _normalize(self, data: dict, now: datetime) -> TrendSignalIn | None:
        if data.get("stickied") or data.get("promoted") or data.get("over_18"):
            return None
        title = clamp_text(data.get("title"), 500)
        external_id = clamp_text(data.get("id") or data.get("name"), 200)
        if not title or not external_id:
            return None
        published_at = utc_from_unix(data.get("created_utc"))
        age_hours = age_hours_between(published_at, now) if published_at else 12.0
        likes = optional_int(data.get("ups") if data.get("ups") is not None else data.get("score"))
        comments = optional_int(data.get("num_comments"))
        views = optional_int(data.get("view_count"))
        permalink = clamp_text(data.get("permalink"), 400)
        url = data.get("url") if isinstance(data.get("url"), str) else None
        if permalink:
            url = f"https://www.reddit.com{permalink}"
        return TrendSignalIn(
            source=self.name,
            external_id=external_id,
            topic=derive_topic(title),
            title=title,
            url=url,
            published_at=published_at,
            views=views,
            likes=likes,
            comments=comments,
            velocity=velocity_from_volume(likes, age_hours),
            engagement_rate=engagement_rate(likes, comments, views),
            age_hours=age_hours,
            cross_platform_count=1,
            source_authority=SOURCE_AUTHORITY,
            risk_score=RISK_SCORE,
            metadata={
                "subreddit": data.get("subreddit"),
                "fullname": data.get("name"),
            },
        )
