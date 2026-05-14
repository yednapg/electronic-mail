from __future__ import annotations

"""Lightweight system and discovery endpoints."""

import sqlite3

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

    if database_backend == "postgres":
        try:
            with get_engine(str(settings.database_path)).connect() as connection:
                revision = connection.exec_driver_sql("SELECT version_num FROM alembic_version LIMIT 1").scalar()
                if revision != ALEMBIC_BASELINE_REVISION:
                    errors.append(
                        f"Database migration revision is {revision or 'missing'}, expected {ALEMBIC_BASELINE_REVISION}"
                    )
                connection.exec_driver_sql("SELECT 1 FROM source_records LIMIT 1")
        except Exception as exc:
            errors.append(f"Postgres readiness check failed: {exc}")
    elif settings.sqlite_database_path is None or not settings.sqlite_database_path.exists():
        errors.append(f"Database file does not exist: {settings.database_path}")
    else:
        try:
            with sqlite3.connect(settings.database_path) as connection:
                row = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'source_records'"
                ).fetchone()
                if row is None:
                    errors.append("Database schema is not initialized")
        except sqlite3.Error as exc:
            errors.append(f"Database readiness check failed: {exc}")

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
        "service": "ElectronicMail Backend",
        "status": "ok",
        "health": "/health",
        "ready": "/ready",
        "feed": "/feed",
        "trace": "/trace/{entity_id}",
        "decide": "/decide",
        "docs": "/docs",
    }
