from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path

from ame.config import get_settings


class ObjectStore(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        raise NotImplementedError

    @abstractmethod
    def get(self, key: str) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def exists(self, key: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def local_path(self, key: str) -> Path:
        raise NotImplementedError

    def sha256(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()


class LocalObjectStore(ObjectStore):
    def __init__(self, root: str | None = None) -> None:
        settings = get_settings()
        self.root = Path(root or settings.storage_local_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe(self, key: str) -> Path:
        cleaned = key.replace("\\", "/").lstrip("/")
        if ".." in Path(cleaned).parts:
            raise ValueError("path traversal rejected")
        path = (self.root / cleaned).resolve()
        if not str(path).startswith(str(self.root)):
            raise ValueError("path traversal rejected")
        return path

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._safe(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return self._safe(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._safe(key).exists()

    def local_path(self, key: str) -> Path:
        return self._safe(key)


class S3ObjectStore(ObjectStore):
    """S3-compatible adapter. Requires runtime credentials; unused in local dry-run."""

    def __init__(self) -> None:
        raise RuntimeError("S3 storage selected but adapter is not configured in this environment")

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        raise NotImplementedError

    def get(self, key: str) -> bytes:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def local_path(self, key: str) -> Path:
        raise NotImplementedError


def get_store() -> ObjectStore:
    settings = get_settings()
    if settings.storage_backend == "s3":
        return S3ObjectStore()
    return LocalObjectStore()
