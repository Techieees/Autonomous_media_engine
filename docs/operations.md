# Operations

V1 local stack is native (Python venv + npm). Docker Compose is optional deploy tooling. Do not point this project at owner production databases.

## Commands

Windows:

| Script | Action |
| --- | --- |
| `./scripts/setup.ps1` | `.env`, `.venv`, backend + dashboard install |
| `./scripts/dev.ps1` | API :8000, dashboard :3000, worker, scheduler |
| `./scripts/stop.ps1` | stop native PIDs in `.ame/pids.json` |
| `./scripts/test.ps1` | `pytest` in `.venv` |
| `./scripts/acceptance.ps1` | `python -m ame.cli.acceptance` |

If local PostgreSQL is missing, AME uses `data/ame.dev.db`. Redis missing is non-fatal. Production still targets PostgreSQL + Redis.

| Make target | Action |
| --- | --- |
| `make setup` | native venv + npm |
| `make test` / `make lint` / `make acceptance` | native `.venv` |
| `make docker-dev` | optional Compose stack |

API process: `uvicorn ame.api.main:app --host 0.0.0.0 --port 8000`. Worker: `python -m ame.jobs.worker`. Scheduler: `python -m ame.jobs.scheduler`.

## Health

`GET http://localhost:8000/api/v1/health` — worker / queue / budget / `dry_run` / ffmpeg.

Dashboard: `http://localhost:3000`.

## Jobs

Worker leases one job at a time (`JobQueue.lease_next`). Failures increment `attempts`, then `RETRY_WAIT` or `DEAD`. `ops.stuck_recovery` (every 2 minutes) returns expired leases to retry.

Inspect recent work: `/api/v1/events`, `/agents`, `/publishing`. Correlation fields on jobs and logs: `correlation_id`, `workflow_id`, `content_id`.

Idempotent enqueue: same `idempotency_key` returns the existing row (`ON CONFLICT DO NOTHING`).

## Storage and data

- Postgres volume `ame_pg`.
- Media volume `ame_storage` → `/data/storage` in api/worker.
- Host default `STORAGE_LOCAL_ROOT=./data/storage` (gitignored).
- Destroying volumes deletes local DB and rendered media.

## Config reload

Compose services read `.env` at start. After changing `DRY_RUN`, LLM/TTS, caps, or OAuth clients: recreate `api`, `worker`, and `scheduler`. `get_settings` is lru-cached per process.

## Failure modes

| Symptom | Check |
| --- | --- |
| Dashboard empty / CORS | `DASHBOARD_ORIGIN`, `CORS_ORIGINS`, api up on 8000 |
| Jobs stuck `leased`/`running` | scheduler + `job_lease_seconds`; `ops.stuck_recovery` |
| `paused_by_budget` | `cost_events` vs daily caps |
| `connection_required` | `/api/v1/bootstrap` / OAuth env; expected if platforms unset |
| Render fail | worker logs; ffmpeg in backend image |
| S3 errors | keep `STORAGE_BACKEND=local` unless S3 is fully configured |

## Isolation

No shared Redis/Postgres with other owner platforms. No production publisher credentials in this repo.
