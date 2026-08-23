"""HTTP health surface of ame.api.main (no OAuth, no live credentials)."""

from __future__ import annotations

import pytest

httpx = pytest.importorskip("httpx")


@pytest.mark.asyncio
async def test_health_routes_return_operational_payload(monkeypatch) -> None:
    pytest.importorskip("ame.api.main")

    async def _db_down() -> dict:
        return {"ok": False, "latency_ms": None, "error": "OperationalError"}

    async def _redis_down() -> dict:
        return {"ok": False, "latency_ms": None, "error": "ConnectionError"}

    monkeypatch.setattr("ame.api.services.ping_db", _db_down)
    monkeypatch.setattr("ame.api.services.ping_redis", _redis_down)

    async def _skip_startup_seed() -> None:
        return None

    monkeypatch.setattr("ame.api.services.run_startup_seed", _skip_startup_seed)

    from ame.api.main import app
    from ame.api.schemas import HealthOut

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/health", "/api/v1/health"):
            response = await client.get(path)
            assert response.status_code == 200, path
            body = response.json()
            parsed = HealthOut.model_validate(body)
            assert parsed.status in {"ok", "degraded", "down"}
            assert parsed.status == "down"
            assert isinstance(parsed.dry_run, bool)
            assert parsed.db.ok is False
            assert parsed.budget.daily_ai_spend_limit > 0
            assert "queued" in parsed.queue.model_dump()
            assert parsed.worker.hint
