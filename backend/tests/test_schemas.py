"""Pydantic contract validation for agent I/O and pipeline payloads."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from ame.contracts.enums import AgentName, AgentRunStatus, ClaimKind
from ame.contracts.schemas import (
    AgentDecision,
    AgentResult,
    FactClaim,
    ResearchPackOut,
    ScriptCandidate,
    TrendSignalIn,
)


def test_agent_result_accepts_succeeded_with_decision() -> None:
    result = AgentResult(
        status=AgentRunStatus.SUCCEEDED,
        output={"scored_count": 1},
        decision=AgentDecision(
            decision="scored_1_opportunities",
            reason="Top signal ranked for research.",
            evidence={"score": 0.71},
            confidence=0.86,
            expected_effect="Director may approve within caps.",
        ),
        events=["opportunity.scored"],
    )
    assert result.status is AgentRunStatus.SUCCEEDED
    assert result.decision is not None
    assert result.decision.confidence == 0.86
    assert result.events == ["opportunity.scored"]
    dumped = result.model_dump()
    assert dumped["status"] == "succeeded"


def test_agent_result_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError) as exc:
        AgentResult(status="kinda_ok", output={})
    assert "status" in str(exc.value)


def test_agent_result_requires_status() -> None:
    with pytest.raises(ValidationError):
        AgentResult(output={"orphan": True})


def test_agent_result_failed_carries_error() -> None:
    result = AgentResult(status=AgentRunStatus.FAILED, error="budget exceeded")
    assert result.output == {}
    assert result.decision is None
    assert result.error == "budget exceeded"


def test_script_candidate_requires_core_fields(script_candidate: ScriptCandidate) -> None:
    assert script_candidate.estimated_duration == 38
    assert script_candidate.hook
    assert script_candidate.claims[0].kind is ClaimKind.REASONABLE_INTERPRETATION
    dumped = script_candidate.model_dump()
    again = ScriptCandidate.model_validate(dumped)
    assert again.cta == script_candidate.cta


@pytest.mark.parametrize(
    "field",
    ("hook", "body", "reveal", "cta", "estimated_duration", "caption"),
)
def test_script_candidate_missing_required_field_fails(
    script_candidate: ScriptCandidate, field: str
) -> None:
    payload = script_candidate.model_dump()
    payload.pop(field)
    with pytest.raises(ValidationError) as exc:
        ScriptCandidate.model_validate(payload)
    assert field in str(exc.value)


def test_script_candidate_rejects_non_int_duration(script_candidate: ScriptCandidate) -> None:
    payload = script_candidate.model_dump()
    payload["estimated_duration"] = "about thirty"
    with pytest.raises(ValidationError):
        ScriptCandidate.model_validate(payload)


def test_trend_signal_in_accepts_fixture_shape(trend_signal: TrendSignalIn) -> None:
    assert trend_signal.source == "hacker_news"
    assert trend_signal.cross_platform_count == 2
    assert trend_signal.velocity == 86.4
    roundtrip = TrendSignalIn.model_validate(trend_signal.model_dump())
    assert roundtrip.external_id == trend_signal.external_id


@pytest.mark.parametrize("field", ("source", "external_id", "topic", "title"))
def test_trend_signal_in_requires_identity_fields(
    trend_signal: TrendSignalIn, field: str
) -> None:
    payload = trend_signal.model_dump()
    payload.pop(field)
    with pytest.raises(ValidationError) as exc:
        TrendSignalIn.model_validate(payload)
    assert field in str(exc.value)


def test_research_pack_out_validates_claims(research_pack: ResearchPackOut) -> None:
    assert research_pack.claims[0].kind is ClaimKind.REASONABLE_INTERPRETATION
    assert research_pack.source_urls
    assert 0.0 <= research_pack.confidence <= 1.0
    again = ResearchPackOut.model_validate(research_pack.model_dump())
    assert again.topic == research_pack.topic


def test_research_pack_out_requires_claims_and_summary() -> None:
    with pytest.raises(ValidationError):
        ResearchPackOut(topic="Fusion", summary="ok")
    with pytest.raises(ValidationError):
        ResearchPackOut(topic="Fusion", claims=[], source_urls=[])


def test_research_pack_out_rejects_unknown_claim_kind() -> None:
    with pytest.raises(ValidationError):
        ResearchPackOut(
            topic="Fusion",
            summary="Notes.",
            claims=[FactClaim(claim="x", kind="made_up")],
            source_urls=["https://example.com"],
        )


def test_agent_decision_nested_in_result_rejects_missing_reason() -> None:
    with pytest.raises(ValidationError):
        AgentResult(
            status=AgentRunStatus.SUCCEEDED,
            decision={"decision": "approve", "confidence": 0.4},
        )
    # related entity id must be a UUID when provided
    with pytest.raises(ValidationError):
        AgentDecision(
            decision="approve",
            reason="top score",
            related_entity_id="not-a-uuid",
        )
    ok = AgentDecision(
        decision="approve",
        reason="top score",
        related_entity_id=uuid4(),
    )
    assert ok.related_entity_id is not None
