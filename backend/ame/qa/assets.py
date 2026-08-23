from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ame.db.models import MediaAsset, ProductionManifestRecord
from ame.storage.base import ObjectStore

RENDER_KINDS = frozenset({"render", "video", "mp4", "final", "output", "short", "vertical"})
CAPTION_KINDS = frozenset({"srt", "vtt", "caption", "captions", "subtitle", "subtitles"})
RENDER_EXTS = (".mp4", ".webm", ".mov", ".mkv")
CAPTION_EXTS = (".srt", ".vtt")
MIN_VIDEO_BYTES = 32
MIN_CAPTION_BYTES = 8
MIN_GENERIC_BYTES = 1
EXTERNAL_USAGE = frozenset({"licensed", "third_party", "stock", "external", "downloaded"})


@dataclass
class LocatedAsset:
    asset: MediaAsset | None
    storage_key: str
    kind: str

    @property
    def label(self) -> str:
        if self.asset is not None:
            return f"{self.asset.kind}:{self.storage_key}"
        return f"{self.kind}:{self.storage_key}"


def pick_render(
    assets: list[MediaAsset], manifest: ProductionManifestRecord | None
) -> LocatedAsset | None:
    ranked = sorted(assets, key=_render_rank)
    for asset in ranked:
        if _is_render(asset):
            return LocatedAsset(asset=asset, storage_key=asset.storage_key, kind=asset.kind)
    spec = (manifest.spec if manifest else None) or {}
    for key in ("output_key", "render_path", "video_path", "media_key"):
        value = spec.get(key)
        if isinstance(value, str) and value.strip():
            return LocatedAsset(asset=None, storage_key=value.strip(), kind="render")
    return None


def pick_captions(
    assets: list[MediaAsset], manifest: ProductionManifestRecord | None
) -> LocatedAsset | None:
    ranked = sorted(assets, key=_caption_rank)
    for asset in ranked:
        if _is_caption(asset):
            return LocatedAsset(asset=asset, storage_key=asset.storage_key, kind=asset.kind)
    spec = (manifest.spec if manifest else None) or {}
    for key in ("subtitle_path", "caption_path", "srt_path"):
        value = spec.get(key)
        if isinstance(value, str) and value.strip():
            return LocatedAsset(asset=None, storage_key=value.strip(), kind="srt")
    return None


def store_exists(store: ObjectStore, key: str) -> bool:
    try:
        return store.exists(key)
    except (OSError, ValueError, FileNotFoundError):
        return False


def asset_size(store: ObjectStore, key: str) -> int | None:
    try:
        path = store.local_path(key)
        if path.exists():
            return path.stat().st_size
    except (OSError, ValueError, NotImplementedError, RuntimeError, FileNotFoundError):
        pass
    try:
        return len(store.get(key))
    except (OSError, ValueError, FileNotFoundError):
        return None


def local_media_path(store: ObjectStore, key: str) -> Path | None:
    try:
        path = store.local_path(key)
    except (OSError, ValueError, NotImplementedError, RuntimeError):
        return None
    return path if path.exists() else None


def read_bytes(store: ObjectStore, key: str) -> bytes | None:
    try:
        return store.get(key)
    except (OSError, ValueError, FileNotFoundError):
        return None


def min_size_for(kind: str, key: str, mime: str | None) -> int:
    if kind in RENDER_KINDS or _ext(key) in RENDER_EXTS or (mime or "").startswith("video/"):
        return MIN_VIDEO_BYTES
    if kind in CAPTION_KINDS or _ext(key) in CAPTION_EXTS:
        return MIN_CAPTION_BYTES
    return MIN_GENERIC_BYTES


def missing_provenance(asset: MediaAsset) -> bool:
    usage = (asset.usage_type or "").lower()
    source = (asset.source or "").lower()
    if usage in EXTERNAL_USAGE or source in EXTERNAL_USAGE:
        return not bool(asset.source_url)
    return False


def _is_render(asset: MediaAsset) -> bool:
    mime = (asset.mime_type or "").lower()
    return (
        asset.kind in RENDER_KINDS
        or _ext(asset.storage_key) in RENDER_EXTS
        or mime.startswith("video/")
    )


def _is_caption(asset: MediaAsset) -> bool:
    mime = (asset.mime_type or "").lower()
    return (
        asset.kind in CAPTION_KINDS
        or _ext(asset.storage_key) in CAPTION_EXTS
        or mime in {"text/srt", "application/x-subrip", "text/vtt"}
    )


def _render_rank(asset: MediaAsset) -> tuple[int, str]:
    prefer = ("render", "video", "final", "output", "mp4", "short", "vertical")
    try:
        return prefer.index(asset.kind), asset.storage_key
    except ValueError:
        return len(prefer), asset.storage_key


def _caption_rank(asset: MediaAsset) -> tuple[int, str]:
    prefer = ("srt", "vtt", "subtitles", "subtitle", "captions", "caption")
    try:
        return prefer.index(asset.kind), asset.storage_key
    except ValueError:
        return len(prefer), asset.storage_key


def _ext(key: str) -> str:
    return Path(key.replace("\\", "/")).suffix.lower()


def source_urls_ok(urls: list[Any] | None) -> list[str]:
    cleaned: list[str] = []
    for item in urls or []:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if value.startswith(("http://", "https://")):
            cleaned.append(value)
    return cleaned
