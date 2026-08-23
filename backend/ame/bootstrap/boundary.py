"""Simulated external platform boundary for acceptance only.

Never marks a real account created. Production uses official API verification.
"""

from __future__ import annotations

from typing import Any

from ame.db.models import AccountBootstrap

BOUNDARY_KEY = "external_boundary"


def read_boundary(row: AccountBootstrap) -> dict[str, Any]:
    payload = dict(row.payload or {})
    raw = payload.get(BOUNDARY_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def write_boundary(row: AccountBootstrap, updates: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row.payload or {})
    current = dict(payload.get(BOUNDARY_KEY) or {})
    current.update(updates)
    payload[BOUNDARY_KEY] = current
    row.payload = payload
    return current


def confirm_simulated_account(row: AccountBootstrap, *, account_id: str = "sim-account") -> None:
    write_boundary(
        row,
        {
            "account_exists": True,
            "account_id": account_id,
            "channel_exists": row.platform == "youtube",
            "professional_account": row.platform == "instagram",
        },
    )


def confirm_simulated_developer_app(row: AccountBootstrap, *, app_id: str = "sim-app") -> None:
    write_boundary(row, {"developer_app_exists": True, "app_id": app_id})


def confirm_simulated_oauth(row: AccountBootstrap, *, subject: str = "sim-oauth") -> None:
    write_boundary(row, {"oauth_authenticated": True, "oauth_subject": subject})
