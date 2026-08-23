# Shared contracts

All specialist implementations MUST use these contracts. Do not invent parallel enums, statuses, or publisher interfaces.

## Content status

See `ame.contracts.enums.ContentStatus`.

## Jobs

See `ame.contracts.enums.JobName`. Jobs are Postgres-backed in `jobs` table. Idempotency key is unique. Publishing uses `publish:{content_id}:{platform}`.

## Agents

Subclass `ame.agents.base.Agent`. I/O is `AgentInput` / `AgentContext` / `AgentResult`.

## Publishers

```python
class PublisherAdapter:
    platform: Platform
    async def validate(self, content, connection) -> ValidationResult
    async def prepare(self, content, asset) -> PreparedPublish
    async def publish(self, prepared, *, idempotency_key: str) -> PublishResult
    async def get_status(self, external_id: str) -> PublishResult
    async def fetch_metrics(self, publication) -> dict
    async def refresh_auth(self, connection) -> ConnectionState
```

## Storage

`ame.storage.ObjectStore` with `put`, `get`, `exists`, `url`. Local filesystem in development.

## LLM

`ame.llm.LLMProvider`: `generate_text`, `generate_structured`, `embed`. Configured by `LLM_PROVIDER`. `dev` provider is deterministic and free.

## TTS

`ame.media.voice.TTSProvider`. `dev` uses espeak-ng / silent-tone fallback.

## Events

See `ame.contracts.events.DOMAIN_EVENTS`.
