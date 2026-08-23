# Autonomous Media Engine (AME)

This repository is an isolated autonomous digital media operation.

Do not inspect, import from, connect to, or modify any sibling repository, Carbon Platform, CTS platform, production database, or external owner infrastructure.

## Product

AME discovers opportunities, researches subjects, writes original short-form scripts, renders 9:16 video, QA-gates assets, publishes through official platform APIs, measures results, and updates strategy.

This is not a copyrighted-video repost bot.

## Architecture

```text
dashboard (Next.js) → FastAPI → PostgreSQL
                          ↓
                   job engine (Postgres-backed)
                          ↓
              runtime agents + FFmpeg + publishers
                          ↓
                         Redis (locks / cache)
```

Authoritative state lives in PostgreSQL. Media files live in object storage (local filesystem in development).

## Ownership

| Area | Path |
| --- | --- |
| Shared contracts | `backend/ame/contracts/` |
| Database models / migrations | `backend/ame/db/`, `backend/alembic/` |
| Job / workflow engine | `backend/ame/jobs/` |
| Runtime agents | `backend/ame/agents/` |
| Trend adapters | `backend/ame/trends/` |
| Scoring / learning | `backend/ame/scoring/`, `backend/ame/learning/` |
| Media / FFmpeg / voice | `backend/ame/media/` |
| Publishers | `backend/ame/publishers/` |
| API | `backend/ame/api/` |
| Dashboard | `dashboard/` |
| Docs | `docs/` |

Do not invent a second architecture. Extend the contracts in `backend/ame/contracts/`.

## Runtime agents

Director, Niche Scout, Trend Scout, Opportunity Scoring, Research, Pattern Analyst, Script Writer, Script Critic, Brand, Media Director, Video Factory, Voice, Subtitle, QA, Analytics, Revenue, Learning.

Agents communicate through persisted tasks, decisions, messages, and events. Prompt chat is not system memory.

Every agent run: receive structured task → load context → do bounded work → validate schema → persist → emit event → return.

## Content lifecycle

`discovered → scored → approved_for_research → researched → scripting → script_selected → production → qa → approved → publishing → published → measuring → learning_complete`

Failure states are explicit: `rejected`, `failed`, `paused_by_budget`, `awaiting_human`, `awaiting_platform_approval`.

## Non-negotiable rules

- Official APIs or explicitly permitted public sources only.
- No watermark removal, DRM bypass, anti-bot evasion, fake engagement, fabricated analytics, or fabricated revenue.
- Simulated records must set `simulation = true`.
- Production publishers must refuse simulated content.
- LLM output is data, never executable code.
- No credentials in Git. Never log tokens, passwords, or secrets.
- Director may change allocation below owner caps. Director may never raise hard spend or volume caps.
- If a platform requires human consent, persist `requires_human_action` and continue the rest of AME.
- Do not request passwords in chat.

## Testing

Test dangerous boundaries: scoring, schema validation, orchestration, idempotency, duplicate publication, budget caps, QA gating, adapters, renderer smoke, workflow integration.

## Local commands

```bash
make setup
make dev
make test
make lint
make down
```
