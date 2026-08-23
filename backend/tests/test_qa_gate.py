"""QA gate: no render rejects; approved path requires a checks object."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ame.contracts.enums import QAVerdict
from ame.contracts.schemas import QAResultOut
from ame.originality.fingerprints import OriginalityReport
from ame.qa.checks import CHECK_KEYS, QABundle, decide_verdict, run_checks
from tests.fakes import MemoryObjectStore


def _empty_originality(topic: str = "robots") -> OriginalityReport:
    return OriginalityReport(
        script_hash="",
        hook_hash="",
        title_normalized=topic,
        asset_manifest_hash="",
        embedding=None,
    )


def _passing_checks() -> dict:
    return {
        name: {"passed": True, "severity": "info", "detail": "ok", "skipped": False}
        for name in CHECK_KEYS
    }


def test_approved_qa_result_requires_checks_object() -> None:
    with pytest.raises(ValidationError):
        QAResultOut(verdict=QAVerdict.APPROVED)
    approved = QAResultOut(verdict=QAVerdict.APPROVED, checks=_passing_checks(), reasons=[])
    assert approved.checks
    assert set(CHECK_KEYS).issubset(approved.checks)
    verdict, reasons = decide_verdict(approved.checks)
    assert verdict is QAVerdict.APPROVED
    assert reasons == []


@pytest.mark.asyncio
async def test_missing_render_is_rejected(tmp_path) -> None:
    bundle = QABundle(
        content=SimpleNamespace(id=uuid4(), topic="Humanoid robotics", opportunity_id=None),
        script=None,
        assets=[],
        research=None,
        opportunity=None,
        manifest=None,
        store=MemoryObjectStore(tmp_path),
        originality=_empty_originality("humanoid robotics"),
    )
    checks = await run_checks(bundle)
    assert "render_exists" in checks
    assert checks["render_exists"]["passed"] is False
    assert checks["render_exists"]["severity"] == "reject"
    verdict, reasons = decide_verdict(checks)
    assert verdict is QAVerdict.REJECTED
    assert any("render" in item for item in reasons)
    out = QAResultOut(verdict=verdict, checks=checks, reasons=reasons)
    assert out.verdict is QAVerdict.REJECTED
    assert out.checks["render_exists"]["passed"] is False


def test_decide_verdict_reject_beats_review() -> None:
    checks = _passing_checks()
    checks["render_exists"] = {
        "passed": False,
        "severity": "reject",
        "detail": "no render/video media asset or manifest output key",
        "skipped": False,
    }
    checks["factual_claims"] = {
        "passed": False,
        "severity": "review",
        "detail": "ambiguous prediction",
        "skipped": False,
    }
    verdict, reasons = decide_verdict(checks)
    assert verdict is QAVerdict.REJECTED
    assert any("render_exists" in item for item in reasons)
