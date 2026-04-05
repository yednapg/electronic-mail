from __future__ import annotations

"""Google OAuth endpoints used to bootstrap local Gmail/Calendar sync."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.core.config import load_settings
from app.services.integrations.google import get_google_auth_url, handle_google_callback


router = APIRouter()
settings = load_settings()


@router.get("/auth/google")
def auth_google() -> RedirectResponse:
    """Start the Google OAuth consent flow."""
    if not settings.google_configured:
        raise HTTPException(status_code=500, detail="Google OAuth is not configured in backend/.env")

    return RedirectResponse(get_google_auth_url(settings))


@router.get("/auth/google/callback")
def auth_google_callback(code: str | None = None) -> RedirectResponse:
    """Exchange the Google OAuth code and send the user back to the dashboard."""
    if code is None:
        raise HTTPException(status_code=400, detail="Missing OAuth code")

    handle_google_callback(settings, code)
    return RedirectResponse(f"{settings.cors_origin}/dashboard")
