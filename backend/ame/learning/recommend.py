from __future__ import annotations

import random
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.db.models import (
    Experiment,
    ExperimentAssignment,
    LearningRecommendation,
    StrategyAllocation,
)
from ame.learning.bandit import MIN_ARM_N, ArmStats, choose_arm, summarize_arms
from ame.learning.features import LearningRecord


def _allocations(rows: list[StrategyAllocation]) -> dict[str, float]:
    active = [row for row in rows if row.active]
    return {row.niche: float(row.allocation) for row in active}


def _recommendation_text(
    chosen: ArmStats | None,
    arms: list[ArmStats],
    allocations: dict[str, float],
    method: str,
) -> str:
    if chosen is None:
        return (
            "Keep niche allocation exploratory. No publication outcomes are available yet. "
            "This is statistical ranking, not an LLM learning claim."
        )
    if method == "exploratory" or chosen.n < MIN_ARM_N:
        return (
            f"Remain exploratory on niche '{chosen.value}' "
            f"(n={chosen.n}, need {MIN_ARM_N} before UCB/Thompson/epsilon-greedy). "
            "Do not concentrate spend yet."
        )
    current = allocations.get(chosen.value)
    if current is None or current < 0.2:
        return (
            f"Increase niche allocation for '{chosen.value}' "
            f"(n={chosen.n}, median_reward={chosen.median:.3f}, method={method}). "
            "Director may shift allocation only below owner caps."
        )
    weak = [arm for arm in arms if arm.n >= MIN_ARM_N and arm.mean < chosen.mean * 0.6]
    if weak:
        worst = min(weak, key=lambda arm: arm.mean)
        if allocations.get(worst.value, 0) >= 0.2:
            return (
                f"Decrease niche allocation for '{worst.value}' "
                f"(n={worst.n}, mean_reward={worst.mean:.3f}) and prefer '{chosen.value}'."
            )
    return (
        f"Keep current mix, with '{chosen.value}' as the strongest mature arm "
        f"(n={chosen.n}, method={method}). Experiments stay locked."
    )


async def lock_experiments(session: AsyncSession) -> list[str]:
    """Lock experiments that have assignments. Never rewrite dimensions."""
    locked_ids: list[str] = []
    experiments = (await session.execute(select(Experiment))).scalars().all()
    for experiment in experiments:
        assignment = await session.scalar(
            select(ExperimentAssignment.id)
            .where(ExperimentAssignment.experiment_id == experiment.id)
            .limit(1)
        )
        if assignment is None:
            continue
        if not experiment.locked:
            experiment.locked = True
            locked_ids.append(str(experiment.id))
    await session.flush()
    return locked_ids


async def persist_recommendation(
    session: AsyncSession,
    *,
    record: LearningRecord,
    history: list[LearningRecord],
    rng: random.Random | None = None,
) -> LearningRecommendation:
    rng = rng or random.Random(str(record.publication_id))
    actual_history = [item for item in history if not item.simulation]
    ranked_history = actual_history or history
    using_simulation_only = not actual_history
    niche_arms = summarize_arms(ranked_history, "niche", rng=rng)
    hook_arms = summarize_arms(ranked_history, "hook_type", rng=rng)
    duration_arms = summarize_arms(ranked_history, "duration_bucket", rng=rng)
    voice_arms = summarize_arms(ranked_history, "voice", rng=rng)
    template_arms = summarize_arms(ranked_history, "visual_template", rng=rng)
    chosen, method = choose_arm(niche_arms, rng=rng)
    if using_simulation_only:
        method = "exploratory"
    allocations = _allocations(
        list((await session.execute(select(StrategyAllocation))).scalars().all())
    )
    text = _recommendation_text(chosen, niche_arms, allocations, method)
    if record.simulation:
        text = (
            f"{text} Based on labeled simulation metrics only; not actual platform performance."
        )
    confidence = 0.25
    if chosen is not None:
        confidence = min(0.85, 0.2 + 0.05 * min(chosen.n, 13))
        if method == "exploratory":
            confidence = min(confidence, 0.35)
    locked = await lock_experiments(session)
    payload = {
        "content_features": record.features.model_dump(mode="json"),
        "targets": record.targets.model_dump(mode="json"),
        "simulation": record.simulation,
        "publication_id": str(record.publication_id),
        "content_id": str(record.content_id),
        "ranking": {
            "niche": [arm.as_dict() for arm in niche_arms],
            "hook_type": [arm.as_dict() for arm in hook_arms],
            "duration_bucket": [arm.as_dict() for arm in duration_arms],
            "voice": [arm.as_dict() for arm in voice_arms],
            "visual_template": [arm.as_dict() for arm in template_arms],
        },
        "policy": {
            "method": method,
            "min_arm_n": MIN_ARM_N,
            "using_simulation_only": using_simulation_only,
            "note": (
                "UCB / epsilon-greedy / Thompson apply only when n>=10 on actual outcomes. "
                "Simulation-only history stays exploratory. This is not LLM learning."
            ),
        },
        "current_allocations": allocations,
        "experiment_lock": {
            "locked_ids": locked,
            "definitions_unchanged": True,
        },
    }
    row = LearningRecommendation(
        id=uuid4(),
        recommendation=text,
        features=payload,
        method=method,
        confidence=confidence,
        consumed=False,
    )
    session.add(row)
    await session.flush()
    return row


def decision_from_recommendation(
    row: LearningRecommendation, related_id: UUID | None
) -> tuple[str, str, dict[str, Any], float]:
    return (
        row.recommendation,
        "Statistical ranking over persisted features/targets. Not an LLM learning step.",
        {
            "method": row.method,
            "features": row.features,
            "recommendation_id": str(row.id),
        },
        row.confidence,
    )
