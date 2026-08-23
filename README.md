# Autonomous Media Engine (AME)

Isolated autonomous media operation. Discovers permitted public signals, researches subjects, writes original short-form scripts, renders 9:16 video, QA-gates assets, publishes only through official platform APIs, measures results, and updates strategy.

This repository is standalone. Do not import from, connect to, or modify sibling repositories, Carbon Platform, CTS, owner production databases, or other owner infrastructure.

This is not a copyrighted-video repost bot.

Authoritative state is PostgreSQL. Media files are object storage (`STORAGE_BACKEND=local` in development). Agents persist tasks, decisions, events, and results. Prompt history is not system memory.

## Layout

```text
dashboard/          Next.js UI (port 3000)
backend/ame/        FastAPI, job engine, agents, media, publishers
backend/alembic/    migrations
docs/               contracts and operator docs
fixtures/           offline trend fallback
docker-compose.yml  postgres, redis, api, worker, scheduler, dashboard
```

```text
dashboard (Next.js) → FastAPI → PostgreSQL
                         ↓
                  job engine (Postgres-backed)
                         ↓
             runtime agents + FFmpeg + publishers
                         ↓
                        Redis (locks / cache)
```

## 1. Install

V1 development is native. Docker is optional deploy tooling and is never required.

Windows (PowerShell):

```powershell
./scripts/setup.ps1
```

This creates `.env` if missing, a `.venv`, installs `backend[dev]`, and `dashboard` npm packages. If local PostgreSQL is unreachable, AME uses `data/ame.dev.db` (SQLite). Redis is optional locally.

POSIX:

```bash
make setup
```

Do not commit `.env`. Change `SECRET_KEY` before any non-local use.

## 2. Launch

Windows:

```powershell
./scripts/dev.ps1
```

| Process    | Role                          | URL / note  |
| ---------- | ----------------------------- | ----------- |
| dashboard  | Next.js operator UI           | http://localhost:3000 |
| api        | FastAPI `ame.api.main:app`    | http://127.0.0.1:8000 |
| worker     | `python -m ame.jobs.worker`   | native process |
| scheduler  | `python -m ame.jobs.scheduler`| native process |

Production architecture remains PostgreSQL + Redis + durable workers. Native V1 falls back to SQLite and in-process Redis substitutes when those services are not installed.

Stop: `./scripts/stop.ps1`. Logs: `.ame/logs/`.

Docker Compose remains available as `make docker-dev` if you want containerized Postgres/Redis later.

## 3. Enter dry-run mode

`.env.example` already sets `DRY_RUN=true`. Settings default is the same (`ame.config.Settings.dry_run`).

Keep or set:

```env
APP_ENV=development
DRY_RUN=true
LLM_PROVIDER=dev
TTS_PROVIDER=dev
```

Dry-run runs the real workflow (ingest → score → research → scripts → render → QA → publish path). Simulated publications and metrics must set `simulation=true`. Production publisher adapters must refuse simulated content.

Missing YouTube / Instagram / TikTok connections is expected. The rest of AME continues; platform gaps become `connection_required` / human-action rows, not a fatal boot error.

Trigger a cycle (does not block on render):

```http
POST http://localhost:8000/api/v1/actions/run-cycle
```

## 4. Inspect dashboard

1. Launch stack (`make dev`).
2. Open `http://localhost:3000` (`NEXT_PUBLIC_API_URL=http://localhost:8000`).
3. Confirm API: `http://localhost:8000/api/v1/health` (worker / queue / budget / `dry_run` / ffmpeg).

Useful reads (base `/api/v1`, `limit` default 50 max 200, `offset`):

| Path | Purpose |
| --- | --- |
| `/overview` | daily counts, director decision, system status |
| `/content` | content table |
| `/trends` | trend signals and opportunity decisions |
| `/agents` | runs, tasks, decisions |
| `/publishing` | queue / published / failed / awaiting |
| `/bootstrap` | platform connection state + human checklist |
| `/human-actions` | owner-only items |
| `/events` | recent domain events |
| `/analytics?window=24h` | metrics (`24h` / `7d` / `30d` / `lifetime`) |
| `/revenue` | actual vs forecast, separated |
| `/strategy` | allocations, experiments, learning |

Treat `simulation=true` rows as simulation. Do not read them as real reach or revenue.

## 5. Configure LLM provider

`ame.llm.get_llm()` (`backend/ame/llm/base.py`).

Development (default, deterministic, free):

```env
LLM_PROVIDER=dev
LLM_API_KEY=
LLM_MODEL=dev-local
EMBEDDING_MODEL=dev-local
```

Paid OpenAI-compatible API:

```env
LLM_PROVIDER=openai
LLM_API_KEY=<from environment, never commit>
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=<model id>
```

`LLM_PROVIDER=compatible` uses the same client with `LLM_BASE_URL`. If `LLM_PROVIDER` is `openai` or `compatible` but `LLM_API_KEY` is empty, AME falls back to `DevLLMProvider`. Non-dev construction without a key raises `RuntimeError`.

LLM output is data. Validate with Pydantic. Never execute model output. Paid calls write `cost_events`.

## 6. Configure TTS

Contract: `ame.media.voice.TTSProvider`. Backend image installs `espeak-ng`.

```env
TTS_PROVIDER=dev
TTS_API_KEY=
TTS_VOICE=default
```

`dev` uses espeak-ng, then a valid silent/tone WAV if speech synthesis is unavailable. Set `TTS_API_KEY` only when switching off `dev` to a configured paid provider. Image generation is separate (`IMAGE_PROVIDER=none` by default).

## 7. Connect YouTube later

Do not treat an empty YouTube connection as a startup failure.

When ready:

1. Create a dedicated Google / YouTube brand account (owner, outside AME).
2. Create an OAuth client. Set in `.env`:

```env
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
YOUTUBE_REDIRECT_URI=http://localhost:8000/api/v1/oauth/youtube/callback
```

3. Optional trend ingest only: `YOUTUBE_DATA_API_KEY=` (YouTube Data API, official).
4. Complete OAuth in the dashboard Bootstrap view. Tokens persist on `platform_connections` (encrypted), never in Git or chat.
5. Until connected, publish jobs stay `connection_required` or use the dry-run adapter (`simulation=true`).

Human-only: OAuth consent, MFA, CAPTCHA, platform post confirmation.

## 8. Connect Instagram later

1. Create a dedicated Instagram account and an eligible professional account (owner).
2. Create a Meta app. Set:

```env
META_APP_ID=
META_APP_SECRET=
META_REDIRECT_URI=http://localhost:8000/api/v1/oauth/instagram/callback
INSTAGRAM_GRAPH_VERSION=v21.0
```

3. Complete Meta authorization from Bootstrap. Official Graph API only.
4. Missing auth → `connection_required` / `requires_human_action`. AME continues dry-run work.

## 9. Connect TikTok later

1. Create a dedicated TikTok account and developer application (owner).
2. Set:

```env
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
TIKTOK_REDIRECT_URI=http://localhost:8000/api/v1/oauth/tiktok/callback
```

3. Complete platform authorization / review if required (`needs_platform_review`, `awaiting_platform_required_approval`).
4. Do not scrape, automate login, or bypass review.

## 10. Run tests

```powershell
./scripts/test.ps1
./scripts/acceptance.ps1
```

POSIX: `make test`, `make lint`, `make acceptance`. These run against the local `.venv`, not Docker.

See `docs/acceptance-report.md` (template, pending verification). Do not treat an unfilled report as a pass.

## Operator docs

| Doc | Topic |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | services, ownership, state |
| [docs/agent-system.md](docs/agent-system.md) | runtime agents |
| [docs/workflow.md](docs/workflow.md) | content lifecycle and jobs |
| [docs/platform-integrations.md](docs/platform-integrations.md) | YouTube / Instagram / TikTok |
| [docs/bootstrap.md](docs/bootstrap.md) | first run |
| [docs/security.md](docs/security.md) | secrets, isolation, policy |
| [docs/costs.md](docs/costs.md) | owner caps and cost events |
| [docs/operations.md](docs/operations.md) | run, migrate, recover |
| [docs/contracts.md](docs/contracts.md) | shared contracts |
| [docs/api.md](docs/api.md) | dashboard API |
| [docs/implementation-brief.md](docs/implementation-brief.md) | specialist brief |
