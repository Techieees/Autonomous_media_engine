from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from ame.contracts.enums import ConnectionState, Platform, PublishStatus


class ValidationResult(BaseModel):
    ok: bool
    status: PublishStatus
    reasons: list[str] = Field(default_factory=list)


class PreparedPublish(BaseModel):
    content_id: UUID
    platform: Platform
    title: str
    description: str
    media_key: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    simulation: bool = True


class PublishResult(BaseModel):
    status: PublishStatus
    external_id: str | None = None
    url: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    simulation: bool = True


class PublisherAdapter(ABC):
    platform: Platform

    @abstractmethod
    async def validate(self, content: Any, connection: Any) -> ValidationResult:
        raise NotImplementedError

    @abstractmethod
    async def prepare(self, content: Any, asset: Any) -> PreparedPublish:
        raise NotImplementedError

    @abstractmethod
    async def publish(self, prepared: PreparedPublish, *, idempotency_key: str) -> PublishResult:
        raise NotImplementedError

    @abstractmethod
    async def get_status(self, external_id: str) -> PublishResult:
        raise NotImplementedError

    @abstractmethod
    async def fetch_metrics(self, publication: Any) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def refresh_auth(self, connection: Any) -> ConnectionState:
        raise NotImplementedError
