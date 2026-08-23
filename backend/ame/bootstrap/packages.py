"""Prepare every brand, profile, and developer-app field before any browser open."""

from __future__ import annotations

from typing import Any

from ame.bootstrap.identity import (
    BACKUP_HANDLES,
    PRIMARY_HANDLE,
    first_human_checkpoint,
    official_flows,
    official_oauth_start_path,
    persist_avatar,
    persist_banner,
    profile_for,
)
from ame.config import Settings
from ame.contracts.enums import Platform


YOUTUBE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)
INSTAGRAM_SCOPES = (
    "instagram_basic",
    "instagram_content_publish",
    "pages_show_list",
    "pages_read_engagement",
)
TIKTOK_SCOPES = ("user.info.basic", "video.upload", "video.publish")


def build_activation_package(
    platform: str,
    *,
    brand: dict[str, Any],
    handle: str,
    settings: Settings,
) -> dict[str, Any]:
    avatar = persist_avatar(brand["name"])
    banner = persist_banner(brand["name"]) if platform == Platform.YOUTUBE.value else None
    profile = profile_for(platform, brand, handle)
    origin = settings.dashboard_origin.rstrip("/")
    flows = official_flows(platform)
    return {
        "brand_name": brand["name"],
        "primary_handle": PRIMARY_HANDLE,
        "backup_handles": list(BACKUP_HANDLES),
        "selected_handle": handle,
        "bio": profile["bio"],
        "description": profile["description"],
        "category": profile["category"],
        "content_pillars": profile["content_pillars"],
        "publishing_strategy": profile["publishing_strategy"],
        "avatar_path": avatar,
        "banner_path": banner,
        "website_description": brand["short_description"],
        "oauth_redirect_uri": _redirect_uri(platform, settings),
        "oauth_start": official_oauth_start_path(platform),
        "privacy_policy_url": f"{origin}/legal/privacy",
        "terms_url": f"{origin}/legal/terms",
        "developer_app": _developer_app(platform, brand, settings),
        "requested_scopes": _scopes(platform),
        "official_flows": flows,
        "signup_url": flows["signup"],
        "handoff_url": _initial_handoff_url(platform),
        "first_checkpoint": first_human_checkpoint(platform),
        "publishing_integration": {
            "format": "9:16",
            "duration_s": brand.get("publishing_strategy", {}).get("duration_s", [35, 45]),
            "dry_run_until_ready": True,
        },
    }


def _developer_app(platform: str, brand: dict[str, Any], settings: Settings) -> dict[str, Any]:
    origin = settings.dashboard_origin.rstrip("/")
    return {
        "name": f"{brand['name']} {platform.title()}",
        "description": brand["short_description"],
        "icon_path": persist_avatar(brand["name"]),
        "redirect_uri": _redirect_uri(platform, settings),
        "privacy_policy_url": f"{origin}/legal/privacy",
        "terms_url": f"{origin}/legal/terms",
        "category": brand["category"],
        "contact_site": origin,
    }


def _scopes(platform: str) -> list[str]:
    if platform == Platform.YOUTUBE.value:
        return list(YOUTUBE_SCOPES)
    if platform == Platform.INSTAGRAM.value:
        return list(INSTAGRAM_SCOPES)
    return list(TIKTOK_SCOPES)


def _redirect_uri(platform: str, settings: Settings) -> str:
    if platform == Platform.YOUTUBE.value:
        return settings.youtube_redirect_uri
    if platform == Platform.INSTAGRAM.value:
        return settings.meta_redirect_uri
    return settings.tiktok_redirect_uri


def _initial_handoff_url(platform: str) -> str:
    flows = official_flows(platform)
    if platform == Platform.YOUTUBE.value:
        return flows["channel"]
    return flows["signup"]
