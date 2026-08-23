"""Content lifecycle is an explicit state machine; invalid jumps are documented."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from ame.contracts.enums import ContentStatus, JobName
from ame.pipeline.advance import (
    BLOCKED_STATUSES,
    STAGE_JOB,
    STAGE_ORDER,
    _STATUS_RANK,
    _Snapshot,
    _next_missing_stage,
    idempotency_key,
)

# Happy path from AGENTS.md / ContentStatus. Failure states are terminal and
# must never be inferred from a missing field.
HAPPY_PATH: tuple[ContentStatus, ...] = (
    ContentStatus.DISCOVERED,
    ContentStatus.SCORED,
    ContentStatus.APPROVED_FOR_RESEARCH,
    ContentStatus.RESEARCHED,
    ContentStatus.SCRIPTING,
    ContentStatus.SCRIPT_SELECTED,
    ContentStatus.PRODUCTION,
    ContentStatus.QA,
    ContentStatus.APPROVED,
    ContentStatus.PUBLISHING,
    ContentStatus.PUBLISHED,
    ContentStatus.MEASURING,
    ContentStatus.LEARNING_COMPLETE,
)

FAILURE_STATES: frozenset[ContentStatus] = frozenset(
    {
        ContentStatus.REJECTED,
        ContentStatus.FAILED,
        ContentStatus.PAUSED_BY_BUDGET,
        ContentStatus.AWAITING_HUMAN,
        ContentStatus.AWAITING_PLATFORM_APPROVAL,
    }
)

# Invalid jump documented here: DISCOVERED must not skip to PUBLISHED.
# Only the immediate successor (or an explicit failure state) is allowed.
ALLOWED_FROM_DISCOVERED = frozenset({ContentStatus.SCORED}) | FAILURE_STATES


def _successors(status: ContentStatus) -> frozenset[ContentStatus]:
    if status in FAILURE_STATES:
        return frozenset()
    index = HAPPY_PATH.index(status)
    nxt: set[ContentStatus] = set(FAILURE_STATES)
    if index + 1 < len(HAPPY_PATH):
        nxt.add(HAPPY_PATH[index + 1])
    return frozenset(nxt)


def test_every_content_status_is_classified() -> None:
    assert set(ContentStatus) == set(HAPPY_PATH) | set(FAILURE_STATES)
    assert set(BLOCKED_STATUSES) <= {item.value for item in FAILURE_STATES}


def test_happy_path_matches_rank_table() -> None:
    for index, status in enumerate(HAPPY_PATH):
        assert _STATUS_RANK[status.value] == index
    assert _STATUS_RANK[ContentStatus.PUBLISHED.value] > _STATUS_RANK[ContentStatus.DISCOVERED.value]


def test_invalid_jump_discovered_to_published_is_not_allowed() -> None:
    assert ContentStatus.PUBLISHED not in ALLOWED_FROM_DISCOVERED
    assert ContentStatus.PUBLISHED not in _successors(ContentStatus.DISCOVERED)
    assert ContentStatus.SCORED in _successors(ContentStatus.DISCOVERED)
    # Rank gap documents the skip: published is many stages after discovered.
    assert (
        _STATUS_RANK[ContentStatus.PUBLISHED.value]
        - _STATUS_RANK[ContentStatus.DISCOVERED.value]
        > 1
    )


def test_pipeline_stages_are_explicit_and_ordered() -> None:
    assert STAGE_ORDER == (
        "research",
        "pattern",
        "scripts",
        "critic",
        "media",
        "voice",
        "subs",
        "render",
        "qa",
        "publish",
        "analytics",
        "learning",
    )
    assert STAGE_JOB["research"] == JobName.RESEARCH.value
    assert STAGE_JOB["publish"] == JobName.PUBLISH.value
    assert STAGE_JOB["qa"] == JobName.QA_CHECK.value


def test_publish_idempotency_key_is_content_and_platform(content_id) -> None:
    assert idempotency_key("publish", content_id, "youtube") == f"publish:{content_id}:youtube"
    assert idempotency_key("research", content_id) == f"research:{content_id}"


def test_next_missing_stage_starts_at_research_not_publish() -> None:
    snapshot = _Snapshot(
        SimpleNamespace(
            id=uuid4(),
            status=ContentStatus.APPROVED_FOR_RESEARCH.value,
            selected_script_id=None,
        ),
        has_research=False,
        script_count=0,
        selected=False,
        has_manifest=False,
        has_voice=False,
        has_subs=False,
        has_video=False,
        qa=None,
        publications=[],
        has_metrics=False,
        jobs=[],
        pattern_succeeded=False,
        learning_succeeded=False,
    )
    assert _next_missing_stage(snapshot, "dry_run") == "research"
    assert _next_missing_stage(snapshot, "dry_run") != "publish"
