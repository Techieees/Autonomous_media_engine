# Platform integrations

Publishers implement `ame.publishers.base.PublisherAdapter` only. Isolated adapters. Official APIs or explicitly permitted public sources. No authenticated-page scraping, watermark removal, DRM bypass, rate-limit evasion, fake engagement, CAPTCHA defeat, or consent-flow circumvention.

Platforms: `youtube` · `instagram` · `tiktok` · `dry_run` (`ame.contracts.enums.Platform`).

## Adapter contract

```text
validate(content, connection) → ValidationResult
prepare(content, asset) → PreparedPublish
publish(prepared, *, idempotency_key) → PublishResult
get_status(external_id) → PublishResult
fetch_metrics(publication) → dict
refresh_auth(connection) → ConnectionState
```

Production `publish` must refuse `simulation=true`. Dry-run writes must set `simulation=true`. Never fabricate a successful publication.

Publish job key: `publish:{content_id}:{platform}`. Rows: `publishing_jobs`, `publications`.

## Connection state

`platform_connections.state` (`ConnectionState`):

`not_configured` · `connection_required` · `connected` · `needs_reauthorization` · `needs_platform_review` · `ready` · `requires_human_action`

Publish statuses include `connection_required`, `requires_human_action`, `awaiting_platform_required_approval`, `rejected_simulation`.

Tokens: `token_encrypted` / `refresh_encrypted` via `ame.security.secrets.encrypt_secret`. Never log raw tokens. Never collect passwords in the UI (OAuth redirects only).

## Trend sources (not publishers)

Allowed in settings: Hacker News (`HACKER_NEWS_ENABLED`), RSS (`RSS_FEEDS`), YouTube Data API (`YOUTUBE_DATA_API_KEY`), Reddit API when credentials are set (`REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` / `REDDIT_USER_AGENT`). Official or explicitly permitted public endpoints only.

## YouTube (later)

Env: `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REDIRECT_URI` (default `http://localhost:8000/api/v1/oauth/youtube/callback`). Optional `YOUTUBE_DATA_API_KEY` for public trend reads.

Owner creates the brand account and OAuth client. AME starts OAuth; callback is the configured redirect. Until `ready`, ingest/research/dry-run continue.

## Instagram (later)

Env: `META_APP_ID`, `META_APP_SECRET`, `META_REDIRECT_URI` (default `http://localhost:8000/api/v1/oauth/instagram/callback`), `INSTAGRAM_GRAPH_VERSION` (default `v21.0`).

Requires an eligible professional account and Meta authorization. Graph API only.

## TikTok (later)

Env: `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI` (default `http://localhost:8000/api/v1/oauth/tiktok/callback`).

If the platform requires review or per-post confirmation, persist that state and wait. Do not bypass.

## Human-only

CAPTCHA, MFA, OAuth consent, KYC, payout, legal acceptance, platform-required post confirmation. Persist `human_actions` and `GET /api/v1/human-actions` / `/bootstrap`.
