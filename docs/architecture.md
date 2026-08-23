# Architecture

AME is one isolated stack: `dashboard/` → FastAPI (`backend/ame/api/`) → PostgreSQL, plus a Postgres-backed job engine, runtime agents, FFmpeg, official publishers, and Redis.

Do not invent a second architecture. Extend `backend/ame/contracts/`. Do not connect this repo to Carbon, CTS, sibling repos, or owner production systems.

## Runtime

```text
dashboard (Next.js :3000)
        ↓  HTTP /api/v1
api     uvicorn ame.api.main:app (:8000)
        ↓
postgres  authoritative state
        ↓
jobs table  →  worker (ame.jobs.worker)  →  agents / media / publishers
scheduler   →  enqueue periodic JobName ticks
redis       locks / cache (ame.redis_client.get_redis)
storage     ObjectStore (local FS in development)
```

`docker-compose.yml` services: `postgres` (16), `redis` (7), `api`, `worker`, `scheduler`, `dashboard`. Shared volume `ame_storage` → `/data/storage`. Postgres volume `ame_pg`.

Compose overrides `DATABASE_URL`, `DATABASE_URL_SYNC`, `REDIS_URL`, and `STORAGE_LOCAL_ROOT` for in-network hostnames. Images: `docker/Dockerfile.backend` (Python 3.12, ffmpeg, espeak-ng, fonts-dejavu-core) and `docker/Dockerfile.dashboard` (Node 22).

## Ownership

| Area | Path |
| --- | --- |
| Shared contracts | `backend/ame/contracts/` |
| Settings | `backend/ame/config.py` |
| Database models / migrations | `backend/ame/db/`, `backend/alembic/` |
| Job / workflow engine | `backend/ame/jobs/` |
| Runtime agents | `backend/ame/agents/` |
| Trend adapters | `backend/ame/trends/` |
| Scoring / learning | `backend/ame/scoring/`, `backend/ame/learning/` |
| Media / FFmpeg / voice | `backend/ame/media/` |
| QA | `backend/ame/qa/` |
| Publishers | `backend/ame/publishers/` |
| Analytics / revenue | `backend/ame/analytics/`, `backend/ame/revenue/` |
| LLM | `backend/ame/llm/` |
| Costs | `backend/ame/costs/` |
| Storage | `backend/ame/storage/` |
| Security | `backend/ame/security/` |
| API | `backend/ame/api/` |
| Acceptance CLI | `backend/ame/cli/` |
| Dashboard | `dashboard/` |
| Fixtures | `fixtures/` |
| Docs | `docs/` |

Job handlers are registered in `backend/ame/jobs/registry.py` and imported from `backend/ame/jobs/handlers.py`. Handler signature: `async def handle_*(session: AsyncSession, job: Job) -> None`.

## State

PostgreSQL is the system of record. Prompt chat is not memory.

| Concern | Tables |
| --- | --- |
| Pipeline | `trend_signals`, `opportunities`, `content_items`, `research_packs`, `scripts`, `production_manifests`, `media_assets`, `qa_results`, `originality_fingerprints` |
| Jobs / audit | `jobs`, `system_events`, `cost_events` |
| Agents | `agent_tasks`, `agent_runs`, `agent_messages`, `agent_decisions` |
| Publish | `publishing_jobs`, `publications`, `platform_connections` |
| Measure | `metric_snapshots`, `revenue_events` |
| Strategy | `experiments`, `experiment_assignments`, `strategy_allocations`, `learning_recommendations` |
| Owner ops | `human_actions`, `brand_configs` |

Media bytes: `ame.storage.ObjectStore` (`put` / `get` / `exists` / `local_path`). Development: `LocalObjectStore` under `STORAGE_LOCAL_ROOT`. Path traversal is rejected. `STORAGE_BACKEND=s3` raises unless a configured adapter exists.

Enums, job names, publisher interface, and domain events: `docs/contracts.md` and `backend/ame/contracts/`.

## Development fallbacks

Defined in `docs/implementation-brief.md` and settings:

- Trends: Hacker News public API + RSS (`HACKER_NEWS_ENABLED`, `RSS_FEEDS`). Network failure → `fixtures/trends/sample.json`.
- LLM: `DevLLMProvider` when `LLM_PROVIDER=dev`.
- TTS: espeak-ng, else a valid WAV tone.
- Publish: dry-run adapter writes `Publication` with `simulation=true`.
