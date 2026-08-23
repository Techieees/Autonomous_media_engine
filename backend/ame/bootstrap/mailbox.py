"""Optional mailbox provider for platform verification emails.

Never stores mailbox passwords. Does not consume security-sensitive codes
unless a future connected provider is explicitly permitted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol


@dataclass(frozen=True)
class VerificationMessage:
    platform: str
    kind: str
    subject: str
    excerpt: str
    link: str | None = None
    code: str | None = None
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class MailboxProvider(Protocol):
    async def list_verification_messages(self, platform: str) -> list[VerificationMessage]: ...


class NullMailbox:
    async def list_verification_messages(self, platform: str) -> list[VerificationMessage]:
        _ = platform
        return []


class SimulatedMailbox:
    """Acceptance-only inbox. Codes are surfaced, never auto-consumed."""

    def __init__(self) -> None:
        self._messages: list[VerificationMessage] = []

    def inject(self, message: VerificationMessage) -> None:
        self._messages.append(message)

    async def list_verification_messages(self, platform: str) -> list[VerificationMessage]:
        return [item for item in self._messages if item.platform == platform]


_SIMULATED = SimulatedMailbox()


def mailbox_provider(*, simulated: bool) -> MailboxProvider:
    if simulated:
        return _SIMULATED
    return NullMailbox()


def simulated_mailbox() -> SimulatedMailbox:
    return _SIMULATED


def surface_messages(messages: list[VerificationMessage]) -> list[dict[str, str | None]]:
    return [
        {
            "platform": item.platform,
            "kind": item.kind,
            "subject": item.subject,
            "excerpt": item.excerpt,
            "link": item.link,
            "code": item.code,
            "received_at": item.received_at.isoformat(),
            "auto_consumed": "false",
        }
        for item in messages
    ]
