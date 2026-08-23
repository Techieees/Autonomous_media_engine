# Implementation brief for specialist agents

Repository root: `C:/Users/FlorianDemir/Desktop/Autonomous_media_engine`

This is an isolated new project. Do not touch sibling repositories.

## Already defined (do not reinvent)

- Contracts: `backend/ame/contracts/`
- Settings: `backend/ame/config.py`
- Models: `backend/ame/db/models/`
- Agent base: `backend/ame/agents/base.py`
- Publisher base: `backend/ame/publishers/base.py`
- Job queue: `backend/ame/jobs/queue.py`
- LLM: `backend/ame/llm/base.py`
- Costs: `backend/ame/costs/tracker.py`
- Storage: `backend/ame/storage/base.py`
- Handler imports: `backend/ame/jobs/handlers.py`

## Handler signature

```python
async def handle_*(session: AsyncSession, job: Job) -> None:
    ...
```

Use `ame.jobs.queue.enqueue` to create follow-up work. Use unique idempotency keys.

Publishing key: `publish:{content_id}:{platform}`

## Pipeline

Trend ingest → score opportunities → Director approves one (budget permitting) → create ContentItem → research → pattern → N scripts → critic selects one → media plan → voice → subtitles → ffmpeg render → QA → dry-run publish if `DRY_RUN` or platform not connected → analytics snapshot → learning → Director recommendation.

## Dry-run

Exercise the real workflow. Simulated publication/metrics must set `simulation=true`. Never present them as real revenue or real reach.

## Development fallbacks

- Trend: Hacker News public API + RSS. If network fails, load `fixtures/trends/sample.json`.
- LLM: `DevLLMProvider` when `LLM_PROVIDER=dev`.
- TTS: espeak-ng, else generate a valid WAV tone.
- Publish: dry-run adapter writes a Publication with `simulation=true`.

## Forbidden

Passwords in git/chat, fabricated metrics as actual, watermark removal, scraping authenticated pages, raising hard caps, TODO stubs for required V1 behavior.
