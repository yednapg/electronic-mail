from __future__ import annotations

"""Google OAuth endpoints used to bootstrap local Gmail/Calendar sync."""

from datetime import datetime, timedelta, timezone
from html import escape
import json
import re
from urllib.parse import parse_qs, urlencode, urlparse

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

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
    create_or_update_user,
    exchange_mobile_code,
    get_current_user,
    hash_token,
    issue_session,
    revoke_request_session,
    save_user_google_tokens,
)
from app.db.repository import (
    create_mobile_oauth_handoff,
    delete_google_oauth_token,
    delete_oauth_login_session,
    delete_user_account_with_google_subject_tombstone,
    get_mobile_oauth_handoff,
    get_oauth_login_session,
    get_user,
    oauth_session_is_after_google_subject_deletion,
    record_google_subject_revocation,
    revoke_user_app_sessions,
)
from app.db.jobs import cancel_user_jobs
from app.db.mail_groups import clear_google_guard_state, delete_user_mail_data, mark_google_disconnected
from app.db.user_mail_guard import (
    exclusive_google_subject_lock,
    exclusive_user_mail_lock,
    google_subject_tombstone_hash,
)
from app.services.integrations.google import get_google_auth_url, handle_google_callback, revoke_stored_google_token, stop_gmail_watch
from app.services.token_crypto import decrypt_json, encrypt_json
from app.services.gmail_watch import ensure_gmail_watch
from app.services.mail_groups import enqueue_first_run


router = APIRouter()
settings = load_settings()
_MOBILE_HANDOFF_TTL_SECONDS = 5 * 60
_NO_STORE_HEADERS = {"Cache-Control": "no-store, private", "Pragma": "no-cache"}
_HANDOFF_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_CODE_CHALLENGE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


@router.get("/auth/google")
def auth_google(redirect_to: str | None = None) -> RedirectResponse:
    """Start the Google OAuth consent flow."""
    if not settings.google_configured:
        raise HTTPException(status_code=500, detail="Google OAuth is not configured in backend/.env")

    if redirect_to is not None and not _is_mobile_handoff_redirect(redirect_to):
        raise HTTPException(status_code=400, detail="Unsupported OAuth redirect target")

    return _no_store_redirect(get_google_auth_url(settings, redirect_to=redirect_to))


@router.get("/v1/auth/google/state", response_model=GoogleAuthState)
def auth_google_state(request: Request) -> GoogleAuthState:
    """Return the current Google connection state without building the dashboard."""
    return auth_state_for_request(settings, request, verify_google_credentials=True)


@router.get("/v1/auth/me", response_model=AuthMeResponse)
def auth_me(request: Request) -> AuthMeResponse:
    """Return the current app user without touching Google APIs."""
    user = get_current_user(settings, request)
    if user is None:
        return AuthMeResponse(authenticated=False, user=None)
    return AuthMeResponse(
        authenticated=True,
        user=AuthUserResponse(id=user.id, email=user.email, display_name=user.display_name, access_enabled=True),
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
    """Delete Gmail-derived data and disconnect Google until explicit reauthorization."""
    user = get_current_user(settings, request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    database_url = str(settings.database_path)
    stored_user = get_user(database_url, user.id)
    if stored_user is None:
        raise HTTPException(status_code=404, detail="Account not found")
    subject_hash = google_subject_tombstone_hash(stored_user.google_sub)
    with exclusive_google_subject_lock(database_url, subject_hash=subject_hash):
        record_google_subject_revocation(database_url, subject_hash=subject_hash)
        try:
            stop_gmail_watch(settings, user_id=user.id)
        except Exception:
            pass
        try:
            revoke_stored_google_token(settings, user_id=user.id)
        except Exception:
            pass
        with exclusive_user_mail_lock(database_url, user_id=user.id):
            # A retained token would make /v1/auth/google/state report connected
            # even though the durable deletion guard rejects every later sync.
            # Remove it so the UI truthfully requires an explicit reconnect and
            # deleted data cannot be silently repopulated by an old replica.
            mark_google_disconnected(database_url, user_id=user.id)
            cancel_user_jobs(database_url, user_id=user.id)
            delete_google_oauth_token(database_url, user_id=user.id)
            delete_user_mail_data(database_url, user_id=user.id)
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
    database_url = str(settings.database_path)
    stored_user = get_user(database_url, user.id)
    if stored_user is None:
        raise HTTPException(status_code=404, detail="Account not found")
    subject_hash = google_subject_tombstone_hash(stored_user.google_sub)

    with exclusive_google_subject_lock(database_url, subject_hash=subject_hash):
        record_google_subject_revocation(database_url, subject_hash=subject_hash)
        try:
            stop_gmail_watch(settings, user_id=user.id)
        except Exception:
            pass
        try:
            revoke_stored_google_token(settings, user_id=user.id)
        except Exception:
            pass
        with exclusive_user_mail_lock(database_url, user_id=user.id):
            mark_google_disconnected(database_url, user_id=user.id)
            cancel_user_jobs(database_url, user_id=user.id)
            delete_google_oauth_token(database_url, user_id=user.id)
            if delete_data:
                delete_user_mail_data(database_url, user_id=user.id)
            if revoke_sessions:
                revoke_user_app_sessions(database_url, user_id=user.id)

    response = Response(status_code=204)
    if revoke_sessions:
        response.delete_cookie(SESSION_COOKIE_NAME, path="/", domain=settings.session_cookie_domain or None)
    return response


@router.delete("/v1/auth/account")
def auth_delete_account(request: Request) -> Response:
    """Permanently revoke and delete the authenticated app account."""
    user = get_current_user(settings, request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    database_url = str(settings.database_path)
    stored_user = get_user(database_url, user.id)
    if stored_user is None:
        raise HTTPException(status_code=404, detail="Account not found")
    subject_hash = google_subject_tombstone_hash(stored_user.google_sub)
    # Lock ordering is always Google subject, then local user. A callback that
    # already won this race finishes first and is subsequently deleted; a
    # callback that loses observes the tombstone before it can recreate a user.
    with exclusive_google_subject_lock(database_url, subject_hash=subject_hash):
        record_google_subject_revocation(database_url, subject_hash=subject_hash)
        try:
            stop_gmail_watch(settings, user_id=user.id)
        except Exception:
            pass
        try:
            revoke_stored_google_token(settings, user_id=user.id)
        except Exception:
            pass
        with exclusive_user_mail_lock(database_url, user_id=user.id):
            cancel_user_jobs(database_url, user_id=user.id)
            delete_user_mail_data(database_url, user_id=user.id)
            revoke_user_app_sessions(database_url, user_id=user.id)
            if not delete_user_account_with_google_subject_tombstone(
                database_url,
                user_id=user.id,
            ):
                raise HTTPException(status_code=404, detail="Account not found")
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", domain=settings.session_cookie_domain or None)
    return response


@router.post("/v1/auth/mobile/exchange", response_model=MobileSessionExchangeResponse)
def auth_mobile_exchange(request: MobileSessionExchangeRequest, response: Response) -> MobileSessionExchangeResponse:
    """Exchange a one-time mobile login code for a bearer session token."""
    response.headers.update(_NO_STORE_HEADERS)
    session = exchange_mobile_code(
        settings,
        code=request.login_code,
        handoff_id=request.handoff_id,
        code_verifier=request.code_verifier,
    )
    return MobileSessionExchangeResponse(
        session_token=session.token,
        expires_at=session.expires_at,
        user=AuthUserResponse(
            id=session.user.id,
            email=session.user.email,
            display_name=session.user.display_name,
            access_enabled=True,
        ),
    )


@router.get("/auth/mobile/complete")
def auth_mobile_complete(handoff_id: str) -> HTMLResponse:
    """Browser landing page after a successful local macOS OAuth handoff."""
    handoff = _read_handoff(handoff_id)
    if handoff is None:
        return HTMLResponse(
            """
            <!doctype html>
            <html>
              <head><title>Electronic Mail</title></head>
              <body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #111; color: #eee; display: grid; min-height: 100vh; place-items: center;">
                <main style="text-align: center;">
                  <h1>Sign-in expired</h1>
                  <p>Please return to Electronic Mail and try again.</p>
                </main>
              </body>
            </html>
            """,
            status_code=410,
            headers=_NO_STORE_HEADERS,
        )

    if handoff[0] == "ready" and handoff[1]:
        callback_params = {"login_code": handoff[1], "handoff_id": handoff_id}
        heading = "Connected. Returning to Electronic Mail..."
        detail = "If the app does not open, return to Electronic Mail and it will finish signing in."
    else:
        callback_params = {
            "status": handoff[0],
            "error": handoff[2] or "Google sign-in did not complete.",
            "handoff_id": handoff_id,
        }
        heading = "Google sign-in did not complete"
        detail = handoff[2] or "Return to Electronic Mail and try again."
    app_callback_url = f"{settings.mobile_redirect_uri}?{urlencode(callback_params)}"
    javascript_app_callback_url = json.dumps(app_callback_url).replace("</", "<\\/")
    return HTMLResponse(
        f"""
        <!doctype html>
        <html>
          <head>
            <title>Electronic Mail</title>
            <meta name="color-scheme" content="dark light">
          </head>
          <body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #111; color: #eee; display: grid; min-height: 100vh; place-items: center;">
            <main style="text-align: center;">
              <h1>{escape(heading)}</h1>
              <p>{escape(detail)}</p>
            </main>
            <script>
              window.location.href = {javascript_app_callback_url};
            </script>
          </body>
        </html>
        """,
        headers=_NO_STORE_HEADERS,
    )


@router.get("/v1/auth/mobile/handoff/{handoff_id}")
def auth_mobile_handoff(handoff_id: str) -> JSONResponse:
    """Return a pending macOS OAuth login code once Google auth completes."""
    handoff = _read_handoff(handoff_id)
    if handoff is None:
        return JSONResponse({"status": "pending"}, status_code=202, headers=_NO_STORE_HEADERS)
    handoff_status, login_code, error = handoff
    if handoff_status == "ready" and login_code:
        return JSONResponse(
            {"status": "ready", "login_code": login_code, "handoff_id": handoff_id},
            headers=_NO_STORE_HEADERS,
        )
    return JSONResponse(
        {"status": handoff_status, "error": error or "Google sign-in did not complete.", "handoff_id": handoff_id},
        headers=_NO_STORE_HEADERS,
    )


@router.get("/auth/google/callback")
def auth_google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Exchange the Google OAuth code and send the user back to the dashboard."""
    oauth_session = (
        get_oauth_login_session(
            str(settings.database_path),
            state=state,
            now=datetime.now(timezone.utc).isoformat(),
        )
        if state
        else None
    )
    redirect_to = oauth_session.redirect_to if oauth_session else None

    if error is not None:
        if state:
            delete_oauth_login_session(str(settings.database_path), state=state)
        handoff_status = "cancelled" if error == "access_denied" else "failed"
        safe_error = "Google sign-in was cancelled." if handoff_status == "cancelled" else "Google sign-in failed. Please try again."
        terminal_response = _persist_terminal_mobile_handoff(
            redirect_to,
            status=handoff_status,
            error=safe_error,
        )
        if terminal_response is not None:
            return terminal_response
        raise HTTPException(status_code=400, detail="Google OAuth did not complete")

    if code is None:
        if state:
            delete_oauth_login_session(str(settings.database_path), state=state)
        terminal_response = _persist_terminal_mobile_handoff(
            redirect_to,
            status="failed",
            error="Google sign-in failed. Please try again.",
        )
        if terminal_response is not None:
            return terminal_response
        raise HTTPException(status_code=400, detail="Missing OAuth code")

    try:
        result = handle_google_callback(settings, code, state)
        if isinstance(result, str):
            return _no_store_redirect(result)
        database_url = str(settings.database_path)
        subject_hash = google_subject_tombstone_hash(result.google_sub)
        with exclusive_google_subject_lock(database_url, subject_hash=subject_hash):
            if not oauth_session_is_after_google_subject_deletion(
                database_url,
                subject_hash=subject_hash,
                oauth_started_epoch=result.oauth_started_epoch,
            ):
                raise RuntimeError("OAuth session predates account deletion. Start again from /auth/google.")
            user = create_or_update_user(
                settings,
                profile=result.profile,
                google_sub=result.google_sub,
                oauth_started_epoch=result.oauth_started_epoch,
            )
            with exclusive_user_mail_lock(database_url, user_id=user.id):
                clear_google_guard_state(
                    database_url,
                    user_id=user.id,
                    oauth_started_epoch=result.oauth_started_epoch,
                )
                save_user_google_tokens(
                    settings,
                    user_id=user.id,
                    tokens=result.tokens,
                    oauth_started_epoch=result.oauth_started_epoch,
                )
            ensure_gmail_watch(settings, user_id=user.id)
            try:
                enqueue_first_run(settings, user_id=user.id)
            except Exception:
                pass
    except HTTPException:
        if state:
            delete_oauth_login_session(str(settings.database_path), state=state)
        terminal_response = _persist_terminal_mobile_handoff(
            redirect_to,
            status="failed",
            error="Google sign-in failed. Please try again.",
        )
        if terminal_response is not None:
            return terminal_response
        raise
    except RuntimeError as exc:
        if state:
            delete_oauth_login_session(str(settings.database_path), state=state)
        terminal_response = _persist_terminal_mobile_handoff(
            redirect_to,
            status="failed",
            error="Google sign-in failed. Please try again.",
        )
        if terminal_response is not None:
            return terminal_response
        if "Start again from /auth/google" in str(exc):
            return _no_store_redirect("/auth/google")
        raise HTTPException(status_code=400, detail="Google sign-in failed. Please try again.") from None
    except Exception:
        if state:
            delete_oauth_login_session(str(settings.database_path), state=state)
        terminal_response = _persist_terminal_mobile_handoff(
            redirect_to,
            status="failed",
            error="Google sign-in failed. Please try again.",
        )
        if terminal_response is not None:
            return terminal_response
        raise HTTPException(status_code=400, detail="Google sign-in failed. Please try again.") from None

    if result.redirect_to is not None and _is_mobile_handoff_redirect(result.redirect_to):
        login_code = create_mobile_code(settings, user_id=user.id)
        context = _mobile_handoff_context(result.redirect_to)
        if context is None:
            raise HTTPException(status_code=400, detail="Invalid mobile handoff")
        handoff_id, code_challenge = context
        create_mobile_oauth_handoff(
            str(settings.database_path),
            handoff_id=handoff_id,
            login_code_encrypted=encrypt_json(settings, {"login_code": login_code}),
            login_code_hash=hash_token(settings, login_code),
            exchange_code_challenge=code_challenge,
            expires_at=(datetime.now(timezone.utc) + timedelta(seconds=_MOBILE_HANDOFF_TTL_SECONDS)).isoformat(),
        )
        return _mobile_completion_redirect(handoff_id)

    issued = issue_session(settings, user=user, platform="web")
    response = RedirectResponse(f"{settings.web_app_url}/post-login", headers=_NO_STORE_HEADERS)
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


def _is_mobile_handoff_redirect(redirect_to: str) -> bool:
    return _mobile_handoff_context(redirect_to) is not None


def _mobile_handoff_context(redirect_to: str) -> tuple[str, str] | None:
    parsed = urlparse(redirect_to)
    expected = urlparse(f"{settings.backend_origin}/auth/mobile/complete")
    if parsed.scheme != expected.scheme or parsed.netloc != expected.netloc or parsed.path != expected.path:
        return None
    values = parse_qs(parsed.query)
    handoff_values = values.get("handoff_id") or []
    challenge_values = values.get("code_challenge") or []
    if len(handoff_values) != 1 or len(challenge_values) != 1:
        return None
    handoff_id = handoff_values[0].strip()
    code_challenge = challenge_values[0].strip()
    if not _HANDOFF_ID_PATTERN.fullmatch(handoff_id) or not _CODE_CHALLENGE_PATTERN.fullmatch(code_challenge):
        return None
    return handoff_id, code_challenge


def _handoff_id_from_redirect(redirect_to: str) -> str | None:
    context = _mobile_handoff_context(redirect_to)
    return context[0] if context else None


def _persist_terminal_mobile_handoff(
    redirect_to: str | None,
    *,
    status: str,
    error: str,
) -> RedirectResponse | None:
    if redirect_to is None:
        return None
    context = _mobile_handoff_context(redirect_to)
    if context is None:
        return None
    handoff_id, code_challenge = context
    create_mobile_oauth_handoff(
        str(settings.database_path),
        handoff_id=handoff_id,
        login_code_encrypted=None,
        login_code_hash=None,
        exchange_code_challenge=code_challenge,
        status=status,
        error=error,
        expires_at=(datetime.now(timezone.utc) + timedelta(seconds=_MOBILE_HANDOFF_TTL_SECONDS)).isoformat(),
    )
    return _mobile_completion_redirect(handoff_id)


def _mobile_completion_redirect(handoff_id: str) -> RedirectResponse:
    return _no_store_redirect(
        f"{settings.backend_origin}/auth/mobile/complete?{urlencode({'handoff_id': handoff_id})}"
    )


def _no_store_redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, headers=_NO_STORE_HEADERS)


def _read_handoff(handoff_id: str) -> tuple[str, str | None, str | None] | None:
    # Delivery is intentionally non-destructive: browser custom-URL handoff and
    # native polling may race. The login code and verifier-bound handoff are
    # consumed together atomically by the exchange endpoint.
    record = get_mobile_oauth_handoff(
        str(settings.database_path),
        handoff_id=handoff_id,
        now=datetime.now(timezone.utc).isoformat(),
    )
    if record is None:
        return None
    if record.status != "ready":
        return record.status, None, record.error
    if not record.login_code_encrypted:
        return "failed", None, "Google sign-in did not complete."
    try:
        payload = decrypt_json(settings, record.login_code_encrypted)
    except Exception:
        return "failed", None, "Google sign-in did not complete."
    login_code = str(payload.get("login_code") or "").strip()
    return ("ready", login_code, None) if login_code else ("failed", None, "Google sign-in did not complete.")
