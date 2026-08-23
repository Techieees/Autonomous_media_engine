# Costs

Hard owner caps live in environment / `Settings`. Director may reallocate below them. Director may never raise them.

UTC day boundary: `ame.costs.tracker._day_start`.

## Owner caps (`.env.example` defaults)

| Setting | Default | Role |
| --- | --- | --- |
| `DAILY_AI_SPEND_LIMIT` | `5.00` | Paid LLM / embedding |
| `DAILY_MEDIA_SPEND_LIMIT` | `2.00` | Paid TTS / image |
| `DAILY_COST_LIMIT` | `7.00` | Combined spend |
| `MAX_CONTENT_PER_DAY` | `6` | Hard volume ceiling |
| `MINIMUM_DAILY_CONTENT` | `0` | Soft floor (not a raiseable hard cap) |
| `TARGET_DAILY_CONTENT` | `2` | Director target |
| `MAXIMUM_DAILY_CONTENT` | `6` | Soft max ≤ hard volume |
| `MAXIMUM_PER_PLATFORM` | `3` | Per-platform volume |
| `MAX_CONCURRENT_AGENT_RUNS` | `4` | Concurrency |
| `MAX_RESEARCH_CALLS_PER_CONTENT` | `4` | Research bound |
| `DEFAULT_CURRENCY` | `EUR` | Display |

## Enforcement

`ame.costs.tracker.assert_budget(session, kind="ai"|"media", extra=0)` sums `cost_events.estimated_cost` and `content_items` created today.

Over limit → `BudgetExceeded` → content/job path `paused_by_budget` / `budget.limit_reached`. Kinds: `ai`, `media`, `total`, `content`.

Every paid provider call must write a `CostEvent` (`record_cost` / `record_cost_sync`): provider, model, tokens, `estimated_cost`, optional job/agent/`content_id`, `kind` (`ai` default, `media` for TTS/image).

`LLM_PROVIDER=dev` records `estimated_cost=0`. OpenAI-compatible path estimates from usage tokens in `ame.llm.base`.

## Revenue

`revenue_events.kind` is `actual` or `forecast` (`RevenueKind`). Never store fabricated platform payout as `actual`. Dry-run / simulated money stays `simulation=true`. `/api/v1/revenue` keeps the two series separate.

## Display

Dashboard `/overview` and `/analytics` must label simulation. Health includes budget snapshot (`GET /api/v1/health`).
