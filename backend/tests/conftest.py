from __future__ import annotations

from uuid import uuid4

import pytest

from ame.config import get_settings
from ame.contracts.schemas import FactClaim, ResearchPackOut, ScriptCandidate, TrendSignalIn


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def trend_signal() -> TrendSignalIn:
    return TrendSignalIn(
        source="hacker_news",
        external_id="fixture-humanoid-robotics",
        topic="Humanoid robotics",
        title="Humanoid robots move from demo to factory floor",
        url="https://news.ycombinator.com/item?id=fixture1",
        views=18400,
        likes=920,
        comments=210,
        velocity=86.4,
        engagement_rate=0.061,
        age_hours=4.0,
        cross_platform_count=2,
        source_authority=0.8,
        risk_score=0.08,
        metadata={"fixture": True},
    )


@pytest.fixture
def script_candidate() -> ScriptCandidate:
    return ScriptCandidate(
        hook="The overlooked reason humanoid robotics suddenly matters.",
        body=(
            "Humanoid robotics is moving from lab curiosity to operational systems. "
            "Three public signals explain the shift: capability, cost, and deployment."
        ),
        reveal="The constraint is no longer the model. It is integration.",
        cta="Follow for the next verified build note.",
        estimated_duration=38,
        on_screen_text=["THE SHIFT", "HUMANOID ROBOTICS", "INTEGRATION"],
        scene_plan=[
            {"at": 0, "text": "THE SHIFT", "duration": 3},
            {"at": 3, "text": "Humanoid robotics", "duration": 12},
        ],
        caption="Humanoid robotics: why the constraint moved.",
        hashtags=["technology", "engineering"],
        sources_used=["https://news.ycombinator.com"],
        claims=[
            FactClaim(
                claim="Public technical discussion of the topic is elevated.",
                kind="reasonable_interpretation",
                sources=["https://news.ycombinator.com"],
                publishable=True,
            )
        ],
    )


@pytest.fixture
def research_pack() -> ResearchPackOut:
    return ResearchPackOut(
        topic="Humanoid robotics",
        summary="Development research pack assembled from permitted public signals.",
        claims=[
            FactClaim(
                claim="Public discussion of this topic is currently elevated.",
                kind="reasonable_interpretation",
                sources=["https://news.ycombinator.com"],
                freshness_checked=True,
                stale=False,
                publishable=True,
            )
        ],
        source_urls=["https://news.ycombinator.com"],
        uncertain_claims=[],
        unsuitable_claims=[],
        confidence=0.72,
    )


@pytest.fixture
def content_id():
    return uuid4()
