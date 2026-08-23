from __future__ import annotations

from functools import lru_cache

from ame.config import get_settings


@lru_cache
def get_redis():
    try:
        import redis
    except ImportError:
        return None
    try:
        client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
        client.ping()
        return client
    except Exception:
        return None
