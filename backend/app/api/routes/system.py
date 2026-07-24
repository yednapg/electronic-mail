from __future__ import annotations

"""Lightweight system and discovery endpoints."""

import logging

from fastapi import APIRouter, HTTPException, Request, status

from app.core.config import load_settings
from app.db.repository import ALEMBIC_HEAD_REVISION
from app.schema_check import (
    ALEMBIC_REVISION_PROBE,
    REQUIRED_SCHEMA_PROBES,
    schema_probe_connection,
)


router = APIRouter()
settings = load_settings()
logger = logging.getLogger(__name__)


@router.get("/health")
def health() -> dict[str, str]:
    """Health probe used by local dev and deployments."""
    return {"status": "ok", "release": getattr(settings, "release_sha", "local")}


@router.get("/ready")
def ready() -> dict[str, object]:
    """Readiness probe for config and database schema availability."""
    errors = settings.readiness_errors()
    database_backend = settings.database_backend

    if database_backend != "postgres":
        errors.append("Runtime database must be Postgres")
    else:
        try:
            with schema_probe_connection(settings) as connection:
                revision = connection.exec_driver_sql(ALEMBIC_REVISION_PROBE).scalar()
                if revision != ALEMBIC_HEAD_REVISION:
                    errors.append(
                        f"Database migration revision is {revision or 'missing'}, expected {ALEMBIC_HEAD_REVISION}"
                    )
                for query in REQUIRED_SCHEMA_PROBES:
                    connection.exec_driver_sql(query)
        except Exception as exc:
            logger.error(
                "postgres.readiness_check_failed",
                extra={
                    "event_fields": {
                        "event": "postgres.readiness_check_failed",
                        "exception_type": type(exc).__name__,
                    }
                },
            )
            errors.append("Postgres readiness check failed")

    if errors:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "not_ready",
                "environment": settings.app_env,
                "errors": errors,
            },
        )

    return {
        "status": "ready",
        "environment": settings.app_env,
        "database": database_backend,
        "google_configured": settings.google_configured,
        "ai_enabled": False,
        "schema_revision": ALEMBIC_HEAD_REVISION,
        "schema_head": ALEMBIC_HEAD_REVISION,
        "release": getattr(settings, "release_sha", "local"),
    }


@router.get("/")
def root(request: Request) -> dict[str, str]:
    """Small index route that advertises the main backend endpoints."""
    runtime_settings = getattr(request.app.state, "settings", settings)
    endpoints = {
        "service": "Electronic Mail Backend",
        "status": "ok",
        "health": "/health",
        "ready": "/ready",
        "google_auth": "/auth/google",
        "app_session": "/v1/app/session",
        "mailbox": "/v1/mailbox",
        "jobs": "/v1/jobs/{job_id}",
        "ops_health": "/v1/ops/health",
    }
    if not runtime_settings.is_production_like:
        endpoints.update(
            {
                "dashboard": "/dashboard",
                "first_run": "/v1/first-run/import-jobs",
                "post_login": "/v1/post-login/readiness",
                "mail_groups": "/v1/mail-groups",
                "tasks": "/v1/tasks",
                "entities": "/v1/entities/{entity_id}/complete",
                "legacy_gmail": "/v1/gmail/threads/{thread_id}/archive",
                "docs": "/docs",
                "redoc": "/redoc",
                "openapi": "/openapi.json",
            }
        )
    return endpoints
