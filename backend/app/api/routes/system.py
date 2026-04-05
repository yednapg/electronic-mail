from __future__ import annotations

"""Lightweight system and discovery endpoints."""

from fastapi import APIRouter


router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Health probe used by local dev and deployments."""
    return {"status": "ok"}


@router.get("/")
def root() -> dict[str, str]:
    """Small index route that advertises the main backend endpoints."""
    return {
        "service": "Decision Pipeline Backend",
        "status": "ok",
        "health": "/health",
        "feed": "/feed",
        "trace": "/trace/{entity_id}",
        "decide": "/decide",
        "docs": "/docs",
    }
