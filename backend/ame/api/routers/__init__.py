from fastapi import APIRouter

from ame.api.routers import (
    actions,
    agents,
    analytics,
    bootstrap,
    content,
    events,
    health,
    human_actions,
    notifications,
    oauth,
    overview,
    publishing,
    reports,
    revenue,
    strategy,
    trends,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.api_router)
api_router.include_router(overview.router)
api_router.include_router(content.router)
api_router.include_router(trends.router)
api_router.include_router(agents.router)
api_router.include_router(strategy.router)
api_router.include_router(analytics.router)
api_router.include_router(revenue.router)
api_router.include_router(publishing.router)
api_router.include_router(bootstrap.router)
api_router.include_router(human_actions.router)
api_router.include_router(actions.router)
api_router.include_router(events.router)
api_router.include_router(oauth.router)
api_router.include_router(reports.router)
api_router.include_router(notifications.router)
