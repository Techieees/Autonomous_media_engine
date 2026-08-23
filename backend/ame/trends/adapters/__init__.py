from ame.trends.adapters.base import TrendAdapter
from ame.trends.adapters.hacker_news import HackerNewsAdapter
from ame.trends.adapters.reddit import RedditAdapter
from ame.trends.adapters.rss import RssAdapter
from ame.trends.adapters.youtube import YouTubeAdapter


def network_adapters() -> tuple[TrendAdapter, ...]:
    return (
        HackerNewsAdapter(),
        RssAdapter(),
        YouTubeAdapter(),
        RedditAdapter(),
    )


__all__ = [
    "HackerNewsAdapter",
    "RedditAdapter",
    "RssAdapter",
    "TrendAdapter",
    "YouTubeAdapter",
    "network_adapters",
]
