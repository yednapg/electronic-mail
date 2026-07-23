from __future__ import annotations

"""FastAPI application entrypoint for the unified backend."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.auth_google import router as auth_google_router
from app.api.routes.app_session import router as app_session_router
from app.api.routes.entities import router as entities_router
from app.api.routes.gmail import router as gmail_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.first_run import router as first_run_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.mail_groups import router as mail_groups_router
from app.api.routes.post_login import router as post_login_router
from app.api.routes.system import router as system_router
from app.api.routes.mailbox import router as mailbox_router
from app.api.routes.tasks import router as tasks_router
from app.core.config import Settings, load_settings
from app.core.observability import RequestObservabilityMiddleware, configure_observability
from app.core.rate_limit import RateLimitMiddleware


settings = load_settings()
configure_observability(settings)


def create_app(runtime_settings: Settings) -> FastAPI:
    """Build the API surface for one resolved deployment environment."""
    production_like = runtime_settings.is_production_like
    application = FastAPI(
        title="Electronic Mail Backend",
        docs_url=None if production_like else "/docs",
        redoc_url=None if production_like else "/redoc",
        openapi_url=None if production_like else "/openapi.json",
    )
    application.state.settings = runtime_settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[runtime_settings.cors_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(
        RateLimitMiddleware,
        enabled=runtime_settings.rate_limit_enabled,
        trust_proxy_headers=production_like,
    )
    application.add_middleware(RequestObservabilityMiddleware, production=production_like)

    application.include_router(system_router)
    application.include_router(auth_google_router)
    application.include_router(app_session_router)
    application.include_router(mailbox_router)
    application.include_router(jobs_router)

    if not production_like:
        application.include_router(first_run_router)
        application.include_router(post_login_router)
        application.include_router(dashboard_router)
        application.include_router(gmail_router)
        application.include_router(tasks_router)
        application.include_router(entities_router)
        application.include_router(mail_groups_router)

    return application


app = create_app(settings)
