from __future__ import annotations

import re
from typing import Any

from ame.contracts.schemas import ProductionManifest

TEMPLATE_ID = "vertical_clean_v1"
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
CANVAS_FPS = 30

_HEX = re.compile(r"#?[0-9A-Fa-f]{6}")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

SECTION_ROLES = ("hook", "body", "reveal", "cta")
SECTION_WEIGHTS = {"hook": 0.16, "body": 0.50, "reveal": 0.24, "cta": 0.10}

ROLE_FONT_SIZE = {"hook": 78, "body": 64, "reveal": 72, "cta": 56}


def default_template() -> dict[str, Any]:
    return {
        "id": TEMPLATE_ID,
        "width": CANVAS_WIDTH,
        "height": CANVAS_HEIGHT,
        "fps": CANVAS_FPS,
        "colors": {
            "background": "#0B0D12",
            "text": "#F4F6F8",
            "accent": "#8EB4FF",
            "muted": "#9AA3B2",
            "subtitle": "#FFFFFF",
        },
        "font_family": "DejaVuSans-Bold",
        "safe_margins": {"top": 180, "bottom": 280, "left": 72, "right": 72},
        "title_size": 78,
        "body_size": 64,
        "subtitle_size": 42,
    }


def normalize_hex(color: str | None, fallback: str) -> str:
    value = (color or "").strip()
    if _HEX.fullmatch(value):
        return f"#{value.removeprefix('#').upper()}"
    return fallback


def hex_to_lavfi(color: str) -> str:
    return f"0x{normalize_hex(color, '#0B0D12').removeprefix('#')}"


def wrap_lines(text: str, width: int = 18, max_lines: int = 6) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    lines: list[str] = []
    current = ""
    for word in cleaned.split(" "):
        trial = word if not current else f"{current} {word}"
        if len(trial) <= width:
            current = trial
            continue
        if current:
            lines.append(current)
        if len(word) <= width:
            current = word
            continue
        for index in range(0, len(word), width):
            chunk = word[index : index + width]
            if len(chunk) == width:
                lines.append(chunk)
                current = ""
            else:
                current = chunk
    if current:
        lines.append(current)
    return "\n".join(lines[:max_lines])


def split_sentences(text: str) -> list[str]:
    value = " ".join((text or "").split())
    if not value:
        return []
    parts = [part.strip() for part in _SENTENCE.split(value) if part.strip()]
    return parts or [value]


def headline(text: str, words: int = 8) -> str:
    parts = (text or "").split()
    if len(parts) <= words:
        return " ".join(parts)
    return " ".join(parts[:words])


def clamp_duration(seconds: int | float | None) -> float:
    raw = float(seconds or 35)
    return max(8.0, min(raw, 90.0))


def manifest_duration(manifest: ProductionManifest | dict[str, Any]) -> float:
    if isinstance(manifest, ProductionManifest):
        scenes = manifest.scenes
    else:
        scenes = manifest.get("scenes", [])
    ends = [float(scene.get("end_s", 0)) for scene in scenes if isinstance(scene, dict)]
    return max(ends) if ends else 35.0


def apply_brand_colors(
    template: dict[str, Any], visual_identity: dict[str, Any] | None
) -> dict[str, Any]:
    colors = dict(template["colors"])
    identity = visual_identity or {}
    for key in ("background", "text", "accent", "subtitle"):
        if key in identity:
            colors[key] = normalize_hex(str(identity[key]), colors[key])
    template = dict(template)
    template["colors"] = colors
    return template


def _section_texts(script: Any) -> dict[str, str]:
    return {
        "hook": (getattr(script, "hook", None) or "").strip(),
        "body": (getattr(script, "body", None) or "").strip(),
        "reveal": (getattr(script, "reveal", None) or "").strip(),
        "cta": (getattr(script, "cta", None) or "").strip(),
    }


def _display_text(
    role: str, voice_text: str, on_screen: list[str], index: int, planned: str
) -> str:
    if planned.strip():
        return planned.strip()
    if index < len(on_screen) and str(on_screen[index]).strip():
        return str(on_screen[index]).strip()
    words = 6 if role in {"hook", "reveal"} else 10
    return headline(voice_text, words=words) or role.upper()


def _timings_from_plan(plan: list[dict[str, Any]], duration_s: float) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    cursor = 0.0
    for index, item in enumerate(plan):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or (SECTION_ROLES[index] if index < 4 else f"scene_{index}"))
        start = float(item.get("at", item.get("start_s", cursor)))
        dur = float(item.get("duration", item.get("duration_s", 0)) or 0)
        if dur <= 0:
            dur = duration_s / max(len(plan), 1)
        raw.append(
            {
                "role": role,
                "start_s": start,
                "duration_s": dur,
                "text": str(item.get("text") or ""),
            }
        )
        cursor = start + dur
    total = raw[-1]["start_s"] + raw[-1]["duration_s"] if raw else 0.0
    scale = duration_s / total if total > 0 else 1.0
    timed: list[dict[str, Any]] = []
    for item in raw:
        start = round(item["start_s"] * scale, 3)
        end = round((item["start_s"] + item["duration_s"]) * scale, 3)
        timed.append({**item, "start_s": start, "end_s": end, "duration_s": round(end - start, 3)})
    if timed:
        timed[-1]["end_s"] = duration_s
        timed[-1]["duration_s"] = round(duration_s - timed[-1]["start_s"], 3)
    return timed


def _weighted_timings(duration_s: float) -> list[dict[str, Any]]:
    cursor = 0.0
    timed: list[dict[str, Any]] = []
    for index, role in enumerate(SECTION_ROLES):
        weight = SECTION_WEIGHTS[role]
        if index == len(SECTION_ROLES) - 1:
            end = duration_s
        else:
            end = round(cursor + duration_s * weight, 3)
        timed.append(
            {
                "role": role,
                "start_s": cursor,
                "end_s": end,
                "duration_s": round(end - cursor, 3),
                "text": "",
            }
        )
        cursor = end
    return timed


def build_production_manifest(
    script: Any,
    *,
    visual_identity: dict[str, Any] | None = None,
) -> ProductionManifest:
    template = apply_brand_colors(default_template(), visual_identity)
    preferred = None
    if isinstance(visual_identity, dict):
        preferred = visual_identity.get("target_duration_s")
    duration_s = clamp_duration(preferred or getattr(script, "estimated_duration", 35))
    sections = _section_texts(script)
    on_screen = [str(item) for item in (getattr(script, "on_screen_text", None) or [])]
    plan = [item for item in (getattr(script, "scene_plan", None) or []) if isinstance(item, dict)]
    timed = _timings_from_plan(plan, duration_s) if plan else _weighted_timings(duration_s)

    colors = template["colors"]
    margins = template["safe_margins"]
    scenes: list[dict[str, Any]] = []
    for index, item in enumerate(timed):
        role = str(item["role"])
        voice_text = sections.get(role) or " ".join(part for part in sections.values() if part)
        display = _display_text(
            role,
            sections.get(role, voice_text),
            on_screen,
            index,
            str(item.get("text") or ""),
        )
        start_s = float(item["start_s"])
        end_s = float(item["end_s"])
        scenes.append(
            {
                "id": role if role in SECTION_ROLES else f"scene_{index}",
                "role": role,
                "start_s": start_s,
                "end_s": end_s,
                "duration_s": round(end_s - start_s, 3),
                "on_screen_text": wrap_lines(display, width=18 if role != "body" else 22),
                "voiceover_text": sections.get(role, voice_text),
                "voiceover_timing": {"start_s": start_s, "end_s": end_s},
                "typography": {
                    "font_family": template["font_family"],
                    "font_size": ROLE_FONT_SIZE.get(role, 64),
                    "font_color": colors["text"],
                    "accent_color": colors["accent"],
                    "align": "center",
                    "line_spacing": 16,
                    "box": True,
                },
                "safe_margins": dict(margins),
                "background_color": colors["background"],
            }
        )

    return ProductionManifest.model_validate(
        {
            "template_id": TEMPLATE_ID,
            "width": CANVAS_WIDTH,
            "height": CANVAS_HEIGHT,
            "fps": CANVAS_FPS,
            "scenes": scenes,
            "voiceover_path": None,
            "subtitle_path": None,
            "music_path": None,
            "thumbnail_path": None,
            "assets": [
                {"kind": "template", **template},
                {
                    "kind": "planner",
                    "script_id": str(getattr(script, "id", "")),
                    "voice_style": getattr(script, "voice_style", None) or "clear_authoritative",
                    "estimated_duration": duration_s,
                    "caption": getattr(script, "caption", None) or "",
                },
            ],
        }
    )
