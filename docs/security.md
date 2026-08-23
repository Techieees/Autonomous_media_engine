# Security

## Isolation

This repository is an isolated media operation. Do not inspect, import from, connect to, or modify sibling repositories, Carbon Platform, CTS, owner production databases, or external owner infrastructure.

## Secrets

- Read from environment / `ame.config.Settings` only (`.env`, never Git).
- `.gitignore` includes `.env`, `*.pem`, `*.key`, `credentials.json`, `client_secret*.json`, `token.json`, `tokens/`.
- Platform tokens on `platform_connections` use `ame.security.secrets.encrypt_secret` / `decrypt_secret` (AES-256-GCM envelope `ame.cred.v1.<kid>.…` keyed by `AME_CREDENTIAL_KEK`, never `SECRET_KEY`).
- `mask_secret` for logs and API error payloads. Never log tokens, cookies, passwords, OAuth secrets, or the KEK.
- Production must fail safely if `AME_CREDENTIAL_KEK` or a non-default `SECRET_KEY` is missing. Do not invent credentials.
- Change `SECRET_KEY` before any shared or production-like deploy. Default example value is not a production secret.
- Production never uses the SQLite development fallback.

## HTTP / OAuth

- CORS: `CORS_ORIGINS` / `DASHBOARD_ORIGIN` (default `http://localhost:3000`).
- Callbacks: `/api/v1/oauth/youtube/callback`, `/instagram/callback`, `/tiktok/callback` (see `.env.example`).
- Validate OAuth `state`. Least-privilege scopes. OAuth redirects only — no password or raw-token forms in the dashboard.

## Storage

`LocalObjectStore._safe` rejects `..` and paths outside `STORAGE_LOCAL_ROOT`. Do not pass user paths through unchecked.

## Policy (non-negotiable)

- Official APIs or explicitly permitted public sources only.
- No watermark removal, DRM bypass, anti-bot evasion, authenticated scraping, fake engagement, fabricated analytics, or fabricated revenue.
- Simulated records: `simulation=true`. Production publishers refuse them.
- LLM output is data; never execute it.
- Director cannot raise `DAILY_AI_SPEND_LIMIT`, `DAILY_MEDIA_SPEND_LIMIT`, or `MAX_CONTENT_PER_DAY`.
- Human-only: CAPTCHA, MFA, OAuth consent, KYC, payout, legal acceptance, platform-required post confirmation.
- AME must not autonomously buy services, change bank details, disable safety, or create fake engagement.

## Logging

Structured JSON (`ame.observability`): `correlation_id`, `workflow_id`, `agent_run_id`, `content_id` when present. No secret fields.
