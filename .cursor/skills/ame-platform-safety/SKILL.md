---
name: ame-platform-safety
description: Official-API and policy restrictions for AME publishers and trend sources. Use when implementing YouTube, Instagram, TikTok, or other platform adapters.
---

# Platform Safety

Implement `PublisherAdapter` only. Isolated adapters. No copy-paste platform clients.

Allowed: official APIs and explicitly permitted public sources (YouTube Data API, Reddit API where permitted, Hacker News, RSS, GDELT).

Forbidden: authenticated-page scraping, watermark removal, DRM bypass, rate-limit evasion, fake engagement, account creation where prohibited, CAPTCHA defeat, consent-flow circumvention.

If OAuth is missing: `connection_required`.
If a human must act: `requires_human_action`.
If the platform requires per-post confirmation: `awaiting_platform_required_approval`.

Never fabricate a successful publication.

Production publishers must refuse `simulation = true` content.
Dry-run publishers must label records `simulation = true`.
