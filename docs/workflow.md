# Workflow

Content status is explicit (`ame.contracts.enums.ContentStatus`). Never infer critical state from missing fields.

## Lifecycle

Happy path:

`discovered → scored → approved_for_research → researched → scripting → script_selected → production → qa → approved → publishing → published → measuring → learning_complete`

Failure / hold states:

`rejected` · `failed` · `paused_by_budget` · `awaiting_human` · `awaiting_platform_approval`

## Pipeline

From `docs/implementation-brief.md` and `JobName`:

1. `trend.ingest` — Hacker News / RSS (or `fixtures/trends/sample.json`).
2. `opportunity.score` — persist `Opportunity` + features.
3. `director.tick` — approve at most what budget and `MAX_CONTENT_PER_DAY` allow; create `ContentItem`.
4. `research.run` — fact pack with provenance.
5. `pattern.analyze` — structural patterns, no copy.
6. `script.generate` — N original candidates.
7. `script.critique` — independent select or reject.
8. `media.plan` → `voice.synth` → `subtitle.build` → `video.render`.
9. `qa.check` — reject or approve. No publish without QA.
10. `publish.run` — if `DRY_RUN=true` or platform not connected, dry-run adapter writes `Publication` with `simulation=true`. Idempotency key: `publish:{content_id}:{platform}`.
11. `analytics.snapshot` at `1h` / `6h` / `24h` / `72h` / `7d` / `30d`.
12. `learning.update` → Director recommendation. `revenue.sync` on its own interval.

`pipeline.advance` moves a content row to the next legal job. Scheduler intervals (`backend/ame/jobs/scheduler.py`): director 5m, trend 15m, niche 6h, stuck recovery 2m, revenue 12h, brand 1d. Scheduler process wakes every 60s.

Manual kick: `POST /api/v1/actions/run-cycle` (enqueue only; does not wait for FFmpeg).

## Jobs

Postgres table `jobs`. Unique `idempotency_key`. Lease: `FOR UPDATE SKIP LOCKED`, `job_lease_seconds` (default 120), `job_max_attempts` (default 5). Retry: exponential backoff capped at 300s (`RETRY_WAIT`). Exhausted jobs: `DEAD` + `dead_letter`.

Statuses: `queued` · `leased` · `running` · `succeeded` · `failed` · `retry_wait` · `dead` · `cancelled`.

Follow-up work: `ame.jobs.queue.enqueue`. Do not invent parallel queues.

## Dry-run

Same pipeline. Label synthetic publications and metrics `simulation=true`. Dashboard and `/revenue` must keep actual vs forecast separate. Production adapters refuse simulated content (`PublishStatus.rejected_simulation`).
