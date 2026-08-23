from __future__ import annotations

import re
from dataclasses import dataclass

_TS = r"(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{1,3})"
_ARROW = re.compile(
    rf"({_TS})\s*-->\s*({_TS})(?:\s+[^\n]*)?",
    re.MULTILINE,
)
_TAG = re.compile(r"<[^>]+>")
_CUE_NUM = re.compile(r"^\d+$")


@dataclass(frozen=True)
class CaptionCue:
    start_s: float
    end_s: float
    text: str

    @property
    def empty(self) -> bool:
        return not self.text.strip()

    @property
    def invalid_bounds(self) -> bool:
        return self.end_s <= self.start_s


def parse_caption_bytes(data: bytes) -> list[CaptionCue]:
    text = data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []
    body = text
    if body.lstrip().upper().startswith("WEBVTT"):
        first_break = body.find("\n")
        body = body[first_break + 1 :] if first_break != -1 else ""
    blocks = re.split(r"\n\s*\n", body.strip())
    cues: list[CaptionCue] = []
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        if lines[0].upper().startswith("NOTE") or lines[0].upper().startswith("STYLE"):
            continue
        stamp_idx = 0
        if _CUE_NUM.match(lines[0]) and len(lines) > 1:
            stamp_idx = 1
        if stamp_idx >= len(lines):
            continue
        stamp = _ARROW.search(lines[stamp_idx])
        if not stamp:
            continue
        start = _parse_ts(stamp.group(1))
        end = _parse_ts(stamp.group(6))
        payload = " ".join(lines[stamp_idx + 1 :])
        payload = _TAG.sub("", payload).replace("\\N", " ").strip()
        cues.append(CaptionCue(start_s=start, end_s=end, text=payload))
    return cues


def caption_boundary_errors(cues: list[CaptionCue]) -> list[str]:
    if not cues:
        return ["no caption cues parsed"]
    errors: list[str] = []
    empty = sum(1 for cue in cues if cue.empty)
    invalid = sum(1 for cue in cues if cue.invalid_bounds)
    if empty:
        errors.append(f"{empty} cue(s) have empty text")
    if invalid:
        errors.append(f"{invalid} cue(s) have end <= start")
    if empty == len(cues):
        errors.append("all caption cues are empty")
    return errors


def _parse_ts(raw: str) -> float:
    match = re.fullmatch(_TS, raw.strip())
    if not match:
        return 0.0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    frac = match.group(4)
    millis = int(frac.ljust(3, "0")[:3])
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0
