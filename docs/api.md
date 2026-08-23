# Dashboard API contract

Base: `/api/v1`

All list endpoints support `limit` (default 50, max 200) and `offset`.

## GET /health

Worker/queue/budget/dry_run/ffmpeg.

## GET /overview

produced_today, published_today, rejected_today, views_today, views_7d, followers_7d, revenue_today, revenue_mtd, experiments_active, winning_topic, director_decision, system_status.

## GET /content

Content table rows with script/status/platform/views/qa.

## GET /trends

Trend signals with scores and opportunity decisions.

## GET /agents

Recent agent runs, tasks, decisions.

## GET /strategy

Allocations, experiment results, learning recommendations.

## GET /analytics?window=24h|7d|30d|lifetime

Normalized metrics plus distributions.

## GET /revenue

actual vs forecast explicitly separated.

## GET /publishing

queued/processing/published/failed/retry/awaiting.

## GET /bootstrap

Platform connection states and human checklist.

## GET /human-actions

Open owner-only items.

## POST /actions/run-cycle

Enqueue an acceptance/director cycle. Does not block on render.

## GET /events

Recent system events.
