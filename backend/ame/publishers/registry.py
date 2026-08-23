from __future__ import annotations

from typing import Any

from ame.config import get_settings
from ame.contracts.enums import Platform
from ame.publishers.base import PublisherAdapter
from ame.publishers.dry_run import DryRunPublisher
from ame.publishers.instagram import InstagramPublisher
from ame.publishers.tiktok import TikTokPublisher
from ame.publishers.youtube import YouTubePublisher

_ADAPTERS: dict[Platform, type[PublisherAdapter]] = {
    Platform.DRY_RUN: DryRunPublisher,
    Platform.YOUTUBE: YouTubePublisher,
    Platform.INSTAGRAM: InstagramPublisher,
    Platform.TIKTOK: TikTokPublisher,
}


def get_adapter(
    platform: Platform | str,
    *,
    client: Any = None,
    access_token: str | None = None,
    connection: Any = None,
) -> PublisherAdapter:
    resolved = Platform(platform)
    adapter_cls = _ADAPTERS[resolved]
    if resolved is Platform.DRY_RUN:
        return DryRunPublisher()
    return adapter_cls(client=client, access_token=access_token, connection=connection)


def adapter_status_matrix() -> dict[str, dict[str, Any]]:
    settings = get_settings()
    youtube_configured = bool(settings.youtube_client_id)
    instagram_configured = bool(settings.meta_app_id)
    tiktok_configured = bool(settings.tiktok_client_key)
    return {
        Platform.DRY_RUN.value: {
            "works_without_credentials": True,
            "publish": True,
            "get_status": True,
            "fetch_metrics": True,
            "refresh_auth": True,
            "validate_without_credentials": "queued",
            "notes": (
                "Always available. Writes Publication records with simulation=true "
                "and ame://simulation URLs. Never a real platform post."
            ),
        },
        Platform.YOUTUBE.value: {
            "works_without_credentials": False,
            "client_configured": youtube_configured,
            "publish": False,
            "get_status": False,
            "fetch_metrics": False,
            "refresh_auth": False,
            "validate_without_credentials": "connection_required",
            "notes": (
                "Official YouTube Data API v3 resumable upload. Requires "
                "YOUTUBE_CLIENT_ID and an OAuth connection. Refuses simulation content. "
                "Does not fabricate success."
            ),
        },
        Platform.INSTAGRAM.value: {
            "works_without_credentials": False,
            "client_configured": instagram_configured,
            "publish": False,
            "get_status": False,
            "fetch_metrics": False,
            "refresh_auth": False,
            "validate_without_credentials": "connection_required",
            "notes": (
                "Official Instagram Graph container + publish for Professional accounts. "
                "Requires META_APP_ID and a linked IG user. Insights call official "
                "endpoints only when a token is present."
            ),
        },
        Platform.TIKTOK.value: {
            "works_without_credentials": False,
            "client_configured": tiktok_configured,
            "publish": False,
            "get_status": False,
            "fetch_metrics": False,
            "refresh_auth": False,
            "validate_without_credentials": "connection_required",
            "notes": (
                "Official Content Posting API Direct Post. Without app review and "
                "unattended-post consent the adapter returns "
                "awaiting_platform_required_approval and a HumanAction. No scraping."
            ),
        },
    }
