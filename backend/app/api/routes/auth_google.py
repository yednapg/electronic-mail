from __future__ import annotations

"""Google OAuth endpoints used to bootstrap local Gmail/Calendar sync."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.core.config import load_settings
from app.services.integrations.google import get_google_auth_url, handle_google_callback


router = APIRouter()
settings = load_settings()


@router.get("/auth/google")
def auth_google(redirect_to: str | None = None) -> RedirectResponse:
    """Start the Google OAuth consent flow."""
    if not settings.google_configured:
        raise HTTPException(status_code=500, detail="Google OAuth is not configured in backend/.env")

    if redirect_to is not None and redirect_to != settings.mobile_redirect_uri:
        raise HTTPException(status_code=400, detail="Unsupported OAuth redirect target")

    return RedirectResponse(get_google_auth_url(settings, redirect_to=redirect_to))


@router.get("/auth/google/callback")
def auth_google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Exchange the Google OAuth code and send the user back to the dashboard."""
    if error is not None:
        raise HTTPException(status_code=400, detail=f"Google OAuth failed: {error}")

    if code is None:
        raise HTTPException(status_code=400, detail="Missing OAuth code")

    try:
        redirect_url = handle_google_callback(settings, code, state)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail="Unable to link this Google account.") from exc

    return RedirectResponse(redirect_url)
