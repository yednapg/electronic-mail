from __future__ import annotations

"""Lightweight system and discovery endpoints."""

import sqlite3

from fastapi import APIRouter, HTTPException, status

from app.core.config import load_settings


router = APIRouter()
settings = load_settings()


@router.get("/health")
def health() -> dict[str, str]:
    """Health probe used by local dev and deployments."""
    return {"status": "ok"}


@router.get("/ready")
def ready() -> dict[str, object]:
    """Readiness probe for config and SQLite schema availability."""
    errors = settings.readiness_errors()

    if not settings.database_path.exists():
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
        "database": "sqlite",
        "google_configured": settings.google_configured,
        "openai_configured": settings.openai_configured,
        "openai_model": settings.openai_model,
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
