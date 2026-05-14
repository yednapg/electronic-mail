from __future__ import annotations

"""Google OAuth endpoints used to bootstrap local Gmail/Calendar sync."""

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse

from app.core.config import load_settings
from app.schemas.domain import (
    AuthMeResponse,
    AuthUserResponse,
    GoogleAuthState,
    MobileSessionExchangeRequest,
    MobileSessionExchangeResponse,
)
from app.services.auth import (
    SESSION_COOKIE_NAME,
    auth_state_for_request,
    create_mobile_code,
    create_or_update_beta_user,
    exchange_mobile_code,
    get_current_user,
    issue_session,
    revoke_request_session,
    save_user_google_tokens,
)
from app.db.repository import delete_google_oauth_token, delete_user_google_data, revoke_user_app_sessions
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


@router.get("/v1/auth/google/state", response_model=GoogleAuthState)
def auth_google_state(request: Request) -> GoogleAuthState:
    """Return the current Google connection state without building the dashboard."""
    return auth_state_for_request(settings, request)


@router.get("/v1/auth/me", response_model=AuthMeResponse)
def auth_me(request: Request) -> AuthMeResponse:
    """Return the current app user without touching Google APIs."""
    user = get_current_user(settings, request)
    if user is None:
        return AuthMeResponse(authenticated=False, user=None)
    return AuthMeResponse(
        authenticated=True,
        user=AuthUserResponse(id=user.id, email=user.email, display_name=user.display_name, beta_enabled=True),
    )


@router.post("/v1/auth/logout")
def auth_logout(request: Request) -> Response:
    """Revoke the current app session."""
    revoke_request_session(settings, request)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", domain=settings.session_cookie_domain or None)
    return response


@router.post("/v1/auth/sessions/revoke")
def auth_revoke_sessions(request: Request) -> Response:
    """Revoke every active app session for the authenticated user."""
    user = get_current_user(settings, request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    revoke_user_app_sessions(str(settings.database_path), user_id=user.id)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", domain=settings.session_cookie_domain or None)
    return response


@router.delete("/v1/auth/google/data")
def auth_delete_google_data(request: Request) -> Response:
    """Delete Gmail-derived data for the authenticated user while keeping Google connected."""
    user = get_current_user(settings, request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    delete_user_google_data(str(settings.database_path), user_id=user.id)
    return Response(status_code=204)


@router.delete("/v1/auth/google")
def auth_google_disconnect(
    request: Request,
    delete_data: bool = Query(default=False, alias="deleteData"),
    revoke_sessions: bool = Query(default=False, alias="revokeSessions"),
) -> Response:
    """Disconnect Google credentials and optionally delete mailbox-derived data."""
    user = get_current_user(settings, request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    delete_google_oauth_token(str(settings.database_path), user_id=user.id)
    if delete_data:
        delete_user_google_data(str(settings.database_path), user_id=user.id)
    if revoke_sessions:
        revoke_user_app_sessions(str(settings.database_path), user_id=user.id)

    response = Response(status_code=204)
    if revoke_sessions:
        response.delete_cookie(SESSION_COOKIE_NAME, path="/", domain=settings.session_cookie_domain or None)
    return response


@router.post("/v1/auth/mobile/exchange", response_model=MobileSessionExchangeResponse)
def auth_mobile_exchange(request: MobileSessionExchangeRequest) -> MobileSessionExchangeResponse:
    """Exchange a one-time mobile login code for a bearer session token."""
    session = exchange_mobile_code(settings, code=request.login_code)
    return MobileSessionExchangeResponse(
        session_token=session.token,
        expires_at=session.expires_at,
        user=AuthUserResponse(
            id=session.user.id,
            email=session.user.email,
            display_name=session.user.display_name,
            beta_enabled=True,
        ),
    )


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
        result = handle_google_callback(settings, code, state)
        if isinstance(result, str):
            return RedirectResponse(result)
        user = create_or_update_beta_user(settings, profile=result.profile, google_sub=result.google_sub)
        save_user_google_tokens(settings, user_id=user.id, tokens=result.tokens)
    except HTTPException:
        raise
    except RuntimeError as exc:
        if "Start again from /auth/google" in str(exc):
            return RedirectResponse("/auth/google")
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if result.redirect_to == settings.mobile_redirect_uri:
        login_code = create_mobile_code(settings, user_id=user.id)
        return RedirectResponse(f"{settings.mobile_redirect_uri}?login_code={login_code}")

    issued = issue_session(settings, user=user, platform="web")
    response = RedirectResponse(f"{settings.web_app_url}/post-login")
    response.set_cookie(
        SESSION_COOKIE_NAME,
        issued.token,
        httponly=True,
        secure=settings.is_production_like,
        samesite=settings.session_cookie_samesite,
        domain=settings.session_cookie_domain or None,
        path="/",
        max_age=30 * 24 * 60 * 60,
    )
    return response
