from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MIN_DURATION_S = 12.0
MAX_DURATION_S = 90.0
REQUIRED_WIDTH = 1080
REQUIRED_HEIGHT = 1920


@dataclass
class ProbeResult:
    available: bool
    error: str | None = None
    has_video: bool = False
    has_audio: bool = False
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def resolution_ok(self) -> bool:
        return self.width == REQUIRED_WIDTH and self.height == REQUIRED_HEIGHT

    @property
    def duration_ok(self) -> bool:
        if self.duration_s is None:
            return False
        return MIN_DURATION_S <= self.duration_s <= MAX_DURATION_S


def ffprobe_executable() -> str | None:
    found = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if found:
        return found
    from ame.media.ffmpeg import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None
    sibling = Path(ffmpeg).with_name("ffprobe.exe" if Path(ffmpeg).suffix.lower() == ".exe" else "ffprobe")
    if sibling.is_file():
        return str(sibling)
    return None


def ffmpeg_probe_executable() -> str | None:
    from ame.media.ffmpeg import find_ffmpeg

    return find_ffmpeg()


async def probe_media(path: Path) -> ProbeResult:
    exe = ffprobe_executable()
    ffmpeg = None if exe else ffmpeg_probe_executable()
    if not exe and not ffmpeg:
        return ProbeResult(available=False, error="ffprobe/ffmpeg not available")
    if not path.exists():
        return ProbeResult(available=True, error=f"media path missing: {path}")

    def _run() -> ProbeResult:
        import subprocess

        if not exe:
            return _probe_with_ffmpeg(path, ffmpeg or "")
        completed = subprocess.run(  # noqa: S603
            [
                exe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or "ffprobe failed").strip()
            return ProbeResult(available=True, error=err[:500])
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            return ProbeResult(available=True, error=f"ffprobe json invalid: {exc}")
        return _from_payload(payload)

    return await asyncio.to_thread(_run)


def _probe_with_ffmpeg(path: Path, ffmpeg: str) -> ProbeResult:
    import re
    import subprocess

    completed = subprocess.run(  # noqa: S603
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    text = f"{completed.stderr or ''}\n{completed.stdout or ''}"
    duration = None
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if match:
        duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
    width = height = None
    video = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", text)
    if video:
        width, height = int(video.group(1)), int(video.group(2))
    return ProbeResult(
        available=True,
        has_video="Video:" in text,
        has_audio="Audio:" in text,
        width=width,
        height=height,
        duration_s=duration,
        raw={"probe": "ffmpeg-i"},
        error=None if "Video:" in text else (text[-400:] or "ffmpeg probe failed"),
    )


def _from_payload(payload: dict[str, Any]) -> ProbeResult:
    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    width, height = _video_dimensions(video)
    duration = _duration(payload, video, audio)
    return ProbeResult(
        available=True,
        has_video=video is not None,
        has_audio=audio is not None,
        width=width,
        height=height,
        duration_s=duration,
        raw={"stream_count": len(streams), "format": payload.get("format", {}).get("format_name")},
    )


def _video_dimensions(video: dict[str, Any] | None) -> tuple[int | None, int | None]:
    if not video:
        return None, None
    try:
        width = int(video.get("width") or 0) or None
        height = int(video.get("height") or 0) or None
    except (TypeError, ValueError):
        return None, None
    rotate = str((video.get("tags") or {}).get("rotate") or "")
    if rotate in {"90", "270"} and width and height:
        return height, width
    return width, height


def _duration(
    payload: dict[str, Any],
    video: dict[str, Any] | None,
    audio: dict[str, Any] | None,
) -> float | None:
    candidates: list[float] = []
    for raw in (
        (payload.get("format") or {}).get("duration"),
        (video or {}).get("duration"),
        (audio or {}).get("duration"),
    ):
        parsed = _as_float(raw)
        if parsed is not None and parsed > 0:
            candidates.append(parsed)
    return max(candidates) if candidates else None


def _as_float(value: Any) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
