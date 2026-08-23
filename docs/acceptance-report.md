# Acceptance report

**Status: pass (native Windows dry-run)**

Recorded after a real in-process run. Docker was not used. PostgreSQL and Redis were not running; AME used `data/ame.dev.db` and treated Redis as optional. Production architecture is unchanged (PostgreSQL + Redis + durable workers).

| Field | Value |
| --- | --- |
| Date | 2026-08-22 |
| Operator | native Windows V1 acceptance (`python -m ame.cli.acceptance`) |
| Git revision | `d611a1411d7f9b2aaa934ba2978f210f424d32d8` |
| Environment | `APP_ENV=development`, `DRY_RUN=true`, `LLM_PROVIDER=dev`, database=`sqlite`, FFmpeg=`imageio-ffmpeg` 7.1 |
| Verdict | **pass** |
| Elapsed | 204.92s |
| Content | `ca54b28b-80cb-4627-b4e7-9aee630c8c76` (`measuring` → later `learning_complete`) |
| Publication | `simulation=true`, platform=`dry_run` |
| MP4 | `data/storage/content/ca54b28b-80cb-4627-b4e7-9aee630c8c76/video.mp4` — 1,063,702 bytes, 1080x1920, 35.0s, audio present, `ftyp` present |

JSON artifact: `backend/ame/cli/last_acceptance.json`.

## How this run was executed

```powershell
./scripts/setup.ps1
./scripts/test.ps1
./scripts/acceptance.ps1
./scripts/dev.ps1
```

API: `http://127.0.0.1:8000`. Dashboard: `http://localhost:3000`.

## Pipeline evidence

| Stage | Result | Evidence |
| --- | --- | --- |
| Trend | pass | HN 38 + RSS 40 official/public adapters; 79 `trend_signals` persisted (SQLite upsert) |
| Opportunity scoring | pass | 79 `opportunities` with bounded scores (example 0.72) |
| Research | pass | 1 `research_packs` for the content id |
| Multiple scripts | pass | 5 script rows |
| Critic selection | pass | 1 selected script |
| Production manifest | pass | 1 `production_manifests` |
| TTS | pass | `voiceover` media asset |
| FFmpeg 1080x1920 | pass | real MP4 via bundled `imageio-ffmpeg` (not PATH) |
| Subtitles | pass | `subtitles` asset |
| QA | pass | 1 `qa_results` then publish |
| Dry-run publication | pass | 1 publication, `simulation=true` |
| Analytics | pass | 1 `metric_snapshots`; content entered `measuring` |
| Learning | pass | `learning.update` job succeeded (`method=exploratory`, `history_n=1`) |
| Director recommendation | pass | Director decision row present |
| Duplicate publish | pass | same job key `publish:{content_id}:dry_run`, still 1 publication |
| Dashboard | pass | HTTP 200 for `/`, `/content`, `/trends`, `/publishing`, `/bootstrap` against live API |

## Boundary checks

| Check | Result | Evidence |
| --- | --- | --- |
| Schema validation (agent I/O, contracts) | pass | `pytest` 55 passed (`backend/tests`) |
| Scoring (`opportunity.score` bounds / explanation) | pass | scoring unit tests + 79 persisted scores |
| Orchestration (job registry → handler → next job) | pass | trend → score → director → research → pattern → scripts → critic → media → voice → subs → render → QA → publish → analytics → learning |
| Idempotency (`jobs.idempotency_key`, `publish:{content_id}:{platform}`) | pass | duplicate publish check |
| Duplicate publication blocked | pass | `jobs_with_key=1 publications=1` |
| Budget caps (`assert_budget`, Director cannot raise hard caps) | pass | unit tests; Director `FORBIDDEN_CAP_KEYS` unchanged |
| QA gating (no publish without QA approve) | pass | QA job before publish; unit QA tests |
| Adapters (official API / dry-run only; production refuses `simulation=true`) | pass | YouTube/IG/TikTok skipped (not configured); dry-run publisher wrote `simulation=true` |
| Renderer smoke (9:16 asset or explicit failure) | pass | `test_render_two_second_1080x1920` + acceptance MP4 |
| Workflow integration (status machine, no inferred state) | pass | content reached `measuring` / `learning_complete` |
| Simulated records labeled `simulation=true` | pass | content + publication |
| Revenue actual vs forecast not mixed | pass | unit tests; no actual revenue rows invented |
| Isolation (no sibling / Carbon / CTS coupling) | pass | this repo only |
| Secrets absent from Git and logs | pass | `.env` gitignored; no tokens logged |
| Missing platform connections non-fatal | pass | youtube/instagram/tiktok `not_configured`; pipeline continued |

## Native fallback (does not change production)

- PostgreSQL unreachable → SQLite `data/ame.dev.db` (`create_all`, WAL).
- Redis unreachable → health `degraded`; OAuth/locks already degrade.
- FFmpeg not on PATH → `imageio-ffmpeg` binary.
- Docker files remain optional (`make docker-dev`).

## Remaining human / external work

- YouTube, Instagram, TikTok OAuth and account consent are not done. That is expected and out of dry-run V1.
- Installing local PostgreSQL/Redis is optional; production still targets them.

## Observed notes

- First acceptance attempt failed on SQLite upsert: `excluded.metadata_json` does not exist because the column is named `metadata`. Fixed, then the full run passed.
- Overview Python filter compared naive SQLite datetimes with aware UTC; API caught the error and still returned 200. `UTCDateTime` + aware compare added so the dashboard overview does not swallow that exception.
- `LearningRecommendation` table stayed empty on this run; `learning.update` still ran and Director wrote a decision. The acceptance check is `learning_or_director_decision`.
- Health is `degraded` without Redis. That is correct, not a fail.

## Verdict

**pass**

Native Windows dry-run acceptance executed end-to-end and produced a real playable 1080x1920 MP4. External account/OAuth work remains owner-only.
