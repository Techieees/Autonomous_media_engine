"""Test doubles that stay inside backend/tests.

Used where Postgres JSONB / unique indexes cannot run in-process, and where
publishers, QA, and the job queue need a deterministic stand-in store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from ame.contracts.enums import JobStatus, Platform, PublishStatus


def publish_key(content_id: UUID | str, platform: Platform | str) -> str:
    """Canonical uniqueness key: publish:{content_id}:{platform}."""
    return f"publish:{content_id}:{platform}"


def job_key(name: str, unique: UUID | str | None = None) -> str:
    return f"{name}:{unique or uuid4()}"


@dataclass
class FakeJob:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    id: UUID = field(default_factory=uuid4)
    status: str = JobStatus.QUEUED.value
    attempts: int = 0
    max_attempts: int = 5
    last_error: str | None = None
    dead_letter: bool = False
    run_after: datetime = field(default_factory=lambda: datetime.now(UTC))
    content_id: UUID | None = None
    workflow_id: UUID | None = None
    correlation_id: str | None = None


class FakeJobStore:
    """In-memory enqueue: the same idempotency_key yields one job."""

    def __init__(self) -> None:
        self.by_key: dict[str, FakeJob] = {}

    def enqueue(
        self,
        name: str,
        payload: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
        content_id: UUID | None = None,
        max_attempts: int = 5,
    ) -> FakeJob:
        key = idempotency_key or job_key(name)
        existing = self.by_key.get(key)
        if existing is not None:
            return existing
        job = FakeJob(
            name=name,
            payload=payload or {},
            idempotency_key=key,
            content_id=content_id,
            max_attempts=max_attempts,
        )
        self.by_key[key] = job
        return job


@dataclass
class FakePublication:
    content_id: UUID
    platform: str
    status: str = PublishStatus.PUBLISHED.value
    simulation: bool = True
    id: UUID = field(default_factory=uuid4)
    reused: bool = False


class FakePublicationStore:
    """content_id + platform is unique; a second publish is a no-op reuse."""

    def __init__(self) -> None:
        self.by_key: dict[str, FakePublication] = {}
        self.publish_calls: int = 0

    def publish(self, content_id: UUID, platform: Platform | str) -> FakePublication:
        key = publish_key(content_id, platform)
        existing = self.by_key.get(key)
        if existing is not None:
            existing.reused = True
            return existing
        self.publish_calls += 1
        record = FakePublication(content_id=content_id, platform=str(platform))
        self.by_key[key] = record
        return record


class ScriptedSession:
    """Async session whose execute().scalar_one() walks a prepared value list."""

    def __init__(self, values: list[Any]) -> None:
        self._values = list(values)
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> SimpleNamespace:
        self.statements.append(stmt)
        if not self._values:
            raise AssertionError("ScriptedSession has no remaining scalar values")
        value = self._values.pop(0)

        def scalar_one() -> Any:
            return value

        return SimpleNamespace(scalar_one=scalar_one)


class MemoryObjectStore:
    """Filesystem-backed store for QA / renderer tests (no path traversal)."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.files: dict[str, bytes] = {}

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
        self.files[key] = data
        return key

    def get(self, key: str) -> bytes:
        if key in self.files:
            return self.files[key]
        return self._safe(key).read_bytes()

    def exists(self, key: str) -> bool:
        return key in self.files or self._safe(key).exists()

    def local_path(self, key: str) -> Path:
        return self._safe(key)

    def sha256(self, data: bytes) -> str:
        import hashlib

        return hashlib.sha256(data).hexdigest()
