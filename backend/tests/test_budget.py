"""Hard spend caps: assert_budget raises BudgetExceeded past DAILY_AI_SPEND_LIMIT."""

from __future__ import annotations

import pytest

from ame.config import get_settings
from ame.costs.tracker import BudgetExceeded, assert_budget
from tests.fakes import ScriptedSession


@pytest.mark.asyncio
async def test_assert_budget_raises_when_ai_spend_exceeds_daily_limit() -> None:
    settings = get_settings()
    spent = settings.daily_ai_spend_limit + 0.25
    session = ScriptedSession([spent])

    with pytest.raises(BudgetExceeded) as exc:
        await assert_budget(session, kind="ai", extra=0.0)

    error = exc.value
    assert error.kind == "ai"
    assert error.spent == spent
    assert error.limit == settings.daily_ai_spend_limit
    assert "paused_by_budget:ai:" in str(error)
    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_assert_budget_raises_when_extra_pushes_over_ai_cap() -> None:
    settings = get_settings()
    spent = settings.daily_ai_spend_limit - 0.05
    session = ScriptedSession([spent])

    with pytest.raises(BudgetExceeded) as exc:
        await assert_budget(session, kind="ai", extra=0.20)

    assert exc.value.kind == "ai"
    assert exc.value.spent == spent


@pytest.mark.asyncio
async def test_assert_budget_allows_spend_at_the_ai_limit() -> None:
    settings = get_settings()
    spent = settings.daily_ai_spend_limit
    # equality is allowed; then total spend and produced_today are also queried
    session = ScriptedSession([spent, spent, 0])
    await assert_budget(session, kind="ai", extra=0.0)
    assert session._values == []


@pytest.mark.asyncio
async def test_assert_budget_under_limit_does_not_raise() -> None:
    session = ScriptedSession([1.0, 1.0, 0])
    await assert_budget(session, kind="ai", extra=0.5)


@pytest.mark.asyncio
async def test_assert_budget_media_and_content_caps() -> None:
    settings = get_settings()
    media_session = ScriptedSession([settings.daily_media_spend_limit + 1.0])
    with pytest.raises(BudgetExceeded) as media_exc:
        await assert_budget(media_session, kind="media")
    assert media_exc.value.kind == "media"

    # kind that is not ai/media still checks total + content count
    total_session = ScriptedSession([settings.daily_cost_limit + 0.01])
    with pytest.raises(BudgetExceeded) as total_exc:
        await assert_budget(total_session, kind="other")
    assert total_exc.value.kind == "total"

    content_session = ScriptedSession([0.0, settings.max_content_per_day])
    with pytest.raises(BudgetExceeded) as content_exc:
        await assert_budget(content_session, kind="other")
    assert content_exc.value.kind == "content"
