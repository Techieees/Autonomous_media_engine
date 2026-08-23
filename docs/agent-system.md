# Agent system

Runtime agents subclass `ame.agents.base.Agent`. I/O is `AgentInput` / `AgentContext` / `AgentResult` (`ame.contracts.schemas`). Names are `ame.contracts.enums.AgentName`.

Agents do not keep invisible in-memory state across runs. They communicate through persisted `agent_tasks`, `agent_runs`, `agent_messages`, `agent_decisions`, and `system_events`. Prompt chat is not system memory.

## Run cycle

Every run (`Agent.run`):

1. Receive structured `AgentInput` (task, payload, optional `content_id` / `workflow_id` / `correlation_id`).
2. Load `AgentContext` from PostgreSQL.
3. Do bounded work in `execute`.
4. Validate output with Pydantic.
5. Persist `AgentRun` and optional `AgentDecision` (who, what, why, evidence, confidence, expected effect, related entity, timestamp).
6. Emit listed domain events onto `system_events`.
7. Return `AgentResult`.

LLM output is data, never executable code.

## Agents

| Agent | `AgentName` | Job | Work |
| --- | --- | --- | --- |
| Director | `director` | `director.tick`, `pipeline.advance` | Approves work under owner caps; changes allocation below caps only; never raises `DAILY_AI_SPEND_LIMIT`, `DAILY_MEDIA_SPEND_LIMIT`, or `MAX_CONTENT_PER_DAY`. |
| Niche Scout | `niche_scout` | `niche.evaluate` | Niche fitness vs current strategy. |
| Trend Scout | `trend_scout` | `trend.ingest` | Permitted public sources only. |
| Opportunity Scoring | `opportunity_scoring` | `opportunity.score` | `OpportunityScore` features and explanation. |
| Research | `research` | `research.run` | `ResearchPackOut`: sources, claim kinds, freshness, no fabricated facts. |
| Pattern Analyst | `pattern_analyst` | `pattern.analyze` | Abstract patterns. No copying copyrighted expression. |
| Script Writer | `script_writer` | `script.generate` | Multiple original `ScriptCandidate` rows. Not the sole reviewer. |
| Script Critic | `script_critic` | `script.critique` | Independent `ScriptCritique`; select one or reject all. |
| Brand | `brand` | `brand.propose` | Brand config proposals (`brand_configs`). |
| Media Director | `media_director` | `media.plan` | `ProductionManifest` (default `vertical_clean_v1`, 1080×1920@30). |
| Voice | `voice` | `voice.synth` | TTS via `TTSProvider`. |
| Subtitle | `subtitle` | `subtitle.build` | Timed captions. |
| Video Factory | `video_factory` | `video.render` | FFmpeg 9:16 render. |
| QA | `qa` | `qa.check` | `QAResultOut` / `QAVerdict`. Gate before publish. |
| Analytics | `analytics` | `analytics.snapshot` | Official metrics at checkpoints. |
| Revenue | `revenue` | `revenue.sync` | `actual` vs `forecast` only; never fabricate. |
| Learning | `learning` | `learning.update` | Recommendations after meaningful checkpoints. |
| Scheduler | `scheduler` | (process) | `ame.jobs.scheduler` enqueues periodic jobs. |

Ops jobs (not conversational agents): `ops.stuck_recovery`, `ops.acceptance_seed`.

## Director bounds

Director may change niche allocation inside owner caps (example: 20% → 30% robotics). Director may not change hard caps, buy services, change bank/payout details, accept legal terms, perform KYC, disable safety, or create fake engagement.

If a platform requires a human, persist `requires_human_action` / a `human_actions` row and continue other work.

## Events

Domain event names: `ame.contracts.events.DOMAIN_EVENTS` (see `docs/contracts.md`).
