from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ame.db.models import ContentItem, Experiment, ExperimentAssignment


DEFAULT_DIMENSIONS = {
    "hook_style": ["question", "statement", "contrast"],
    "duration": [30, 40, 50],
    "subtitle_style": ["sentence", "phrase_emphasis"],
    "title_style": ["plain", "curiosity"],
    "cta": ["follow", "none"],
}


def _pick(content_id: UUID, name: str, options: list) -> object:
    digest = hashlib.sha256(f"{content_id}:{name}".encode()).hexdigest()
    return options[int(digest[:8], 16) % len(options)]


async def assign_experiment(session: AsyncSession, content: ContentItem) -> ExperimentAssignment:
    existing = await session.execute(
        select(ExperimentAssignment).where(ExperimentAssignment.content_id == content.id)
    )
    found = existing.scalar_one_or_none()
    if found:
        return found

    active = await session.execute(select(Experiment).where(Experiment.status == "active").limit(1))
    experiment = active.scalar_one_or_none()
    if experiment is None:
        experiment = Experiment(
            name="core_shortform_v1",
            dimensions=DEFAULT_DIMENSIONS,
            status="active",
            locked=True,
        )
        session.add(experiment)
        await session.flush()

    variant = {
        key: _pick(content.id, key, values)
        for key, values in experiment.dimensions.items()
        if isinstance(values, list) and values
    }
    assignment = ExperimentAssignment(
        experiment_id=experiment.id,
        content_id=content.id,
        variant=variant,
    )
    session.add(assignment)
    content.experiment_id = experiment.id
    await session.flush()
    return assignment
