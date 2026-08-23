# Bootstrap

First launch with no production social accounts is expected. It is not a fatal application error.

AME should run research and dry-run production while the owner completes platform setup later. See `GET /api/v1/bootstrap` and the dashboard Bootstrap / Human Actions views.

## First run

1. Copy `.env.example` → `.env` (`make setup` or manual copy).
2. Leave `DRY_RUN=true`, `LLM_PROVIDER=dev`, `TTS_PROVIDER=dev`.
3. `make setup` then `make dev`.
4. Open `http://localhost:3000` and `http://localhost:8000/api/v1/health`.
5. Confirm `/bootstrap` lists YouTube / Instagram / TikTok as not connected and a human checklist.

Default local secrets in `.env.example` (`POSTGRES_PASSWORD=ame`, `SECRET_KEY=change-me-in-production`) are development only.

## Owner checklist

```text
YouTube
[ ] Dedicated Google/YouTube brand account
[ ] OAuth client env vars
[ ] Complete OAuth callback

Instagram
[ ] Dedicated Instagram account
[ ] Eligible professional account
[ ] Meta app env vars + authorization

TikTok
[ ] Dedicated account
[ ] Developer application
[ ] Authorization / platform review if required

Monetization
[ ] Not yet eligible — do not record fabricated revenue
```

Do not request platform passwords in chat or UI. OAuth only. Do not ask for these connections until the local stack itself is running.

## After connections exist

AME may research, produce, QA-reject, publish where the platform allows unattended posting, fetch official analytics, change allocation below caps, run/stop experiments, retry technical jobs, lower spend, and reduce volume.

AME may not raise hard caps, buy services, change bank/payout data, accept legal/KYC, bypass CAPTCHA or platform approval, fake engagement, disable safety, delete social accounts, or expose credentials.

## Isolation

This project does not bootstrap against Carbon, CTS, or any sibling owner platform. Credentials and databases stay inside this Compose stack.
