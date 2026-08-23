"""Autonomous brand and profile generation. No owner questions."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from ame.config import get_settings
from ame.contracts.enums import Platform

BRAND_NAME = "Signal Brief"
PRIMARY_HANDLE = "signalbrief"
BACKUP_HANDLES = (
    "signalbriefhq",
    "thesignalbrief",
    "signalbrief_lab",
    "signalbrieflabs",
)
PILLARS = ("ai", "robotics", "science", "engineering")
SIMULATED_TAKEN = frozenset({"signalbrief"})


def choose_brand() -> dict[str, Any]:
    return {
        "name": BRAND_NAME,
        "tone": "precise",
        "audience": "Curious operators who want compact, sourced explainers.",
        "voice_personality": "clear_authoritative",
        "content_pillars": list(PILLARS),
        "category": "Science & Technology",
        "short_description": (
            "Short-form explainers on AI, robotics, science, and engineering "
            "from permitted public signals."
        ),
        "title_conventions": "Specific claim or question; no fabricated stakes.",
        "caption_conventions": "One-sentence takeaway; cite sources for factual claims.",
        "publishing_strategy": {
            "format": "9:16",
            "duration_s": [35, 45],
            "cadence": "bounded_by_owner_caps",
            "hook_style": "question",
        },
        "visual_identity": {
            "template": "vertical_clean_v1",
            "palette": "dark",
            "background": "#0B0D12",
            "accent": "#8EB4FF",
        },
    }


def handle_candidates(platform: str) -> list[str]:
    prefix = {
        Platform.YOUTUBE.value: "",
        Platform.INSTAGRAM.value: "",
        Platform.TIKTOK.value: "",
    }.get(platform, "")
    names = [PRIMARY_HANDLE, *BACKUP_HANDLES]
    return [f"{prefix}{name}"[:24] for name in names]


def next_available_handle(platform: str, attempted: list[str] | None = None) -> str | None:
    tried = {item.lower() for item in (attempted or [])}
    for candidate in handle_candidates(platform):
        if candidate.lower() in tried:
            continue
        if _handle_taken(platform, candidate):
            continue
        return candidate
    return None


def _handle_taken(platform: str, handle: str) -> bool:
    settings = get_settings()
    if settings.bootstrap_simulation and handle.lower() in SIMULATED_TAKEN:
        return True
    return False


def profile_for(platform: str, brand: dict[str, Any], handle: str) -> dict[str, Any]:
    bio = {
        Platform.YOUTUBE.value: brand["short_description"],
        Platform.INSTAGRAM.value: "Sourced short-form notes on AI, robotics, and engineering.",
        Platform.TIKTOK.value: "Compact sourced explainers. AI · robotics · science.",
    }.get(platform, brand["short_description"])
    return {
        "account_name": brand["name"],
        "handle": handle,
        "bio": bio[:150],
        "description": brand["short_description"],
        "category": brand["category"],
        "content_pillars": brand["content_pillars"],
        "publishing_strategy": brand["publishing_strategy"],
    }


def official_signup_url(platform: str) -> str:
    return official_flows(platform)["signup"]


def official_oauth_start_path(platform: str) -> str:
    return f"/api/v1/oauth/{platform}/start"


def official_flows(platform: str) -> dict[str, str]:
    return {
        Platform.YOUTUBE.value: {
            "signup": "https://accounts.google.com/signup",
            "channel": "https://www.youtube.com/create_channel",
            "studio": "https://studio.youtube.com",
            "developer": "https://console.cloud.google.com/apis/library/youtube.googleapis.com",
        },
        Platform.INSTAGRAM.value: {
            "signup": "https://www.instagram.com/accounts/emailsignup/",
            "professional": "https://professionaldashboard.instagram.com/",
            "developer": "https://developers.facebook.com/apps/",
        },
        Platform.TIKTOK.value: {
            "signup": "https://www.tiktok.com/signup",
            "developer": "https://developers.tiktok.com/apps",
        },
    }[platform]


def first_human_checkpoint(platform: str) -> str:
    if platform == Platform.YOUTUBE.value:
        return "channel_creation"
    if platform == Platform.INSTAGRAM.value:
        return "email_verification"
    return "email_verification"


def persist_banner(name: str) -> str | None:
    try:
        from PIL import Image, ImageDraw, ImageFont

        settings = get_settings()
        root = Path(settings.storage_local_root)
        path = root / "brand" / "banner.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (2560, 1440), (11, 13, 18))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 1260, 2560, 1440), fill=(142, 180, 255))
        try:
            font = ImageFont.load_default()
        except OSError:
            font = None
        draw.text((80, 80), name, fill=(232, 236, 245), font=font)
        image.save(path, format="PNG")
        return str(path)
    except Exception:
        return None


def generate_avatar_bytes(name: str) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (512, 512), (11, 13, 18))
    draw = ImageDraw.Draw(image)
    draw.ellipse((48, 48, 464, 464), fill=(142, 180, 255))
    initials = "".join(part[0] for part in name.split()[:2]).upper() or "SB"
    try:
        font = ImageFont.load_default()
    except OSError:
        font = None
    draw.text((210, 220), initials, fill=(11, 13, 18), font=font)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def persist_avatar(name: str) -> str | None:
    try:
        settings = get_settings()
        root = Path(settings.storage_local_root)
        path = root / "brand" / "avatar.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(generate_avatar_bytes(name))
        return str(path)
    except Exception:
        return None
