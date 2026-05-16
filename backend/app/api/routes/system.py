from __future__ import annotations

"""Lightweight system and discovery endpoints."""

from fastapi import APIRouter, HTTPException, status

from app.core.config import load_settings
from app.db.repository import ALEMBIC_BASELINE_REVISION, get_engine


router = APIRouter()
settings = load_settings()


@router.get("/health")
def health() -> dict[str, str]:
    """Health probe used by local dev and deployments."""
    return {"status": "ok"}


@router.get("/ready")
def ready() -> dict[str, object]:
    """Readiness probe for config and database schema availability."""
    errors = settings.readiness_errors()
    database_backend = settings.database_backend

    if database_backend != "postgres":
        errors.append("Runtime database must be Postgres")
    else:
        try:
            with get_engine(str(settings.database_path)).connect() as connection:
                revision = connection.exec_driver_sql("SELECT version_num FROM alembic_version LIMIT 1").scalar()
                if revision != ALEMBIC_BASELINE_REVISION:
                    errors.append(
                        f"Database migration revision is {revision or 'missing'}, expected {ALEMBIC_BASELINE_REVISION}"
                    )
                connection.exec_driver_sql("SELECT 1 FROM gmail_messages LIMIT 1")
                connection.exec_driver_sql("SELECT 1 FROM mail_groups LIMIT 1")
                connection.exec_driver_sql("SELECT 1 FROM app_session_snapshots LIMIT 1")
        except Exception as exc:
            errors.append(f"Postgres readiness check failed: {exc}")

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
        "openai_configured": settings.openai_configured,
        "openai_model": settings.openai_model,
        "openai_reasoning_effort": settings.openai_reasoning_effort,
    }


@router.get("/")
def root() -> dict[str, str]:
    """Small index route that advertises the main backend endpoints."""
    return {
        "service": "Mail Groups Backend",
        "status": "ok",
        "health": "/health",
        "ready": "/ready",
        "dashboard": "/dashboard",
        "app_session": "/v1/app/session",
        "mail_groups": "/v1/mail-groups",
        "mailbox": "/v1/mailbox",
        "jobs": "/v1/jobs/{job_id}",
        "ops_health": "/v1/ops/health",
        "docs": "/docs",
    }
