from __future__ import annotations

"""Google OAuth endpoints used to bootstrap local Gmail/Calendar sync."""

from datetime import datetime, timedelta, timezone
from functools import partial
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
)
from app.db.repository import (
    create_mobile_oauth_handoff,
    delete_google_oauth_token,
    delete_oauth_login_session,
    delete_user_account_with_google_subject_tombstone,
    get_mobile_oauth_handoff,
    get_oauth_login_session,
    get_user,
    get_user_by_google_subject,
    oauth_session_is_after_google_subject_deletion,
    reconnect_google_oauth_token,
    record_google_subject_revocation,
    revoke_user_app_sessions,
)
from app.db.jobs import cancel_user_jobs, has_active_google_token_revocation
from app.db.mail_groups import delete_user_mail_data, mark_google_disconnected
from app.db.user_mail_guard import (
    AdvisoryLockUnavailable,
    exclusive_google_subject_lock,
    exclusive_user_mail_lock,
    google_subject_tombstone_hash,
)
from app.services.integrations.google import (
    enqueue_google_token_revocation_payload,
    get_google_auth_url,
    handle_google_callback,
    revoke_google_token_payload,
    revoke_or_enqueue_google_token_payload,
    revoke_or_enqueue_stored_google_token,
    stop_gmail_watch,
)
from app.services.token_crypto import decrypt_json, encrypt_json
from app.services.gmail_watch import ensure_gmail_watch
from app.services.mail_groups import enqueue_first_run


router = APIRouter()
settings = load_settings()
_MOBILE_HANDOFF_TTL_SECONDS = 5 * 60
_NO_STORE_HEADERS = {"Cache-Control": "no-store, private", "Pragma": "no-cache"}
_HANDOFF_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_CODE_CHALLENGE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _prepare_google_token_revocation_or_fail(
    *,
    database_url: str,
    user_id: str,
    subject_hash: str,
) -> None:
    """Keep a failed revocation tracked and block further Gmail work."""
    try:
        revoke_or_enqueue_stored_google_token(
            settings,
            user_id=user_id,
            subject_hash=subject_hash,
        )
    except Exception as exc:
        # Never destroy the only local copy of a provider grant unless it was
        # revoked or durably queued. Close the Gmail guard so the retained
        # credential cannot continue syncing while the user retries cleanup.
        mark_google_disconnected(database_url, user_id=user_id)
        cancel_user_jobs(database_url, user_id=user_id)
        raise HTTPException(
            status_code=503,
            detail="Google authorization cleanup could not be secured. Please try again.",
        ) from exc


def _disconnect_callback_google_connection(*, database_url: str, user_id: str) -> None:
    """Make an existing local grant unusable before callback cleanup can revoke it."""
    with exclusive_user_mail_lock(database_url, user_id=user_id):
        delete_google_oauth_token(database_url, user_id=user_id)
        mark_google_disconnected(database_url, user_id=user_id)
        cancel_user_jobs(database_url, user_id=user_id)


def _cleanup_unpersisted_callback_grant(
    *,
    database_url: str,
    subject_hash: str,
    tokens: dict[str, object],
    user_id: str | None,
) -> None:
    """Best-effort terminal cleanup while the caller owns the subject lock."""
    if user_id is not None:
        try:
            _disconnect_callback_google_connection(
                database_url=database_url,
                user_id=user_id,
            )
        except Exception:
            pass
    try:
        resolved = revoke_google_token_payload(tokens)
    except Exception:
        resolved = False
    if resolved:
        try:
            # Force every later OAuth attempt to have started after this
            # provider cleanup outcome, even when durable job creation failed.
            record_google_subject_revocation(
                database_url,
                subject_hash=subject_hash,
            )
        except Exception:
            pass


def _queue_callback_grant_cleanup(
    *,
    subject_hash: str,
    tokens: dict[str, object],
) -> bool:
    """Try to hand cleanup to a worker that will obey subject ordering."""
    try:
        enqueue_google_token_revocation_payload(
            settings,
            subject_hash=subject_hash,
            tokens=tokens,
        )
    except Exception:
        return False
    return True


def _queue_callback_grant_cleanup_or_revoke(
    *,
    subject_hash: str,
    tokens: dict[str, object],
) -> None:
    """Revoke directly only when durable ordered cleanup cannot be secured."""
    if not _queue_callback_grant_cleanup(subject_hash=subject_hash, tokens=tokens):
        revoke_google_token_payload(tokens)


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
        with exclusive_user_mail_lock(database_url, user_id=user.id):
            # Subject -> user is the global lock order. The exclusive user lock
            # drains provider mutations/watch renewal before cancellation, and
            # remains held until durable guards and data deletion are complete.
            try:
                stop_gmail_watch(settings, user_id=user.id)
            except Exception:
                pass
            _prepare_google_token_revocation_or_fail(
                database_url=database_url,
                user_id=user.id,
                subject_hash=subject_hash,
            )
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
        with exclusive_user_mail_lock(database_url, user_id=user.id):
            try:
                stop_gmail_watch(settings, user_id=user.id)
            except Exception:
                pass
            _prepare_google_token_revocation_or_fail(
                database_url=database_url,
                user_id=user.id,
                subject_hash=subject_hash,
            )
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
        with exclusive_user_mail_lock(database_url, user_id=user.id):
            try:
                stop_gmail_watch(settings, user_id=user.id)
            except Exception:
                pass
            _prepare_google_token_revocation_or_fail(
                database_url=database_url,
                user_id=user.id,
                subject_hash=subject_hash,
            )
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

    unpersisted_google_tokens: dict[str, object] | None = None
    unpersisted_google_subject_hash: str | None = None
    callback_user_id: str | None = None
    oauth_login_session_delete_attempted = False
    try:
        result = handle_google_callback(settings, code, state)
        if isinstance(result, str):
            return _no_store_redirect(result)
        unpersisted_google_tokens = result.tokens
        database_url = str(settings.database_path)
        subject_hash = google_subject_tombstone_hash(result.google_sub)
        unpersisted_google_subject_hash = subject_hash
        with exclusive_google_subject_lock(database_url, subject_hash=subject_hash):
            existing_user = get_user_by_google_subject(database_url, result.google_sub)
            callback_user_id = existing_user.id if existing_user is not None else None
            try:
                if state:
                    oauth_login_session_delete_attempted = True
                    delete_oauth_login_session(database_url, state=state)
                if not oauth_session_is_after_google_subject_deletion(
                    database_url,
                    subject_hash=subject_hash,
                    oauth_started_epoch=result.oauth_started_epoch,
                ):
                    raise RuntimeError("OAuth session predates account deletion. Start again from /auth/google.")
                if has_active_google_token_revocation(
                    database_url,
                    subject_hash=subject_hash,
                ):
                    raise RuntimeError("Previous Google authorization cleanup is still pending. Please try again later.")
                user = create_or_update_user(
                    settings,
                    profile=result.profile,
                    google_sub=result.google_sub,
                    oauth_started_epoch=result.oauth_started_epoch,
                )
                callback_user_id = user.id
                with exclusive_user_mail_lock(database_url, user_id=user.id):
                    # Google revocation is project/user-wide, so replacing an
                    # ordinary active grant must be an atomic overwrite only.
                    reconnect_google_oauth_token(
                        database_url,
                        user_id=user.id,
                        token_json_encrypted=encrypt_json(settings, result.tokens),
                        oauth_started_epoch=result.oauth_started_epoch,
                    )
                    # Ownership transfers only after the atomic guard-clear and
                    # token transaction commits. Later callback work must not
                    # revoke that durable credential.
                    unpersisted_google_tokens = None
                ensure_gmail_watch(settings, user_id=user.id)
                try:
                    enqueue_first_run(settings, user_id=user.id)
                except Exception:
                    pass
            finally:
                if unpersisted_google_tokens is not None:
                    tracked_user_id = callback_user_id
                    try:
                        revoke_or_enqueue_google_token_payload(
                            settings,
                            subject_hash=subject_hash,
                            tokens=unpersisted_google_tokens,
                            on_tracked=(
                                partial(
                                    _disconnect_callback_google_connection,
                                    database_url=database_url,
                                    user_id=tracked_user_id,
                                )
                                if tracked_user_id is not None
                                else None
                            ),
                        )
                    except Exception:
                        # Do not let cleanup ownership escape the subject lock.
                        # A concurrent newer callback must not persist between
                        # this grant failing and its project-wide revocation.
                        _cleanup_unpersisted_callback_grant(
                            database_url=database_url,
                            subject_hash=subject_hash,
                            tokens=unpersisted_google_tokens,
                            user_id=tracked_user_id,
                        )
                    finally:
                        unpersisted_google_tokens = None
    except HTTPException:
        if state and not oauth_login_session_delete_attempted:
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
        if state and not oauth_login_session_delete_attempted:
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
        if state and not oauth_login_session_delete_attempted:
            delete_oauth_login_session(str(settings.database_path), state=state)
        terminal_response = _persist_terminal_mobile_handoff(
            redirect_to,
            status="failed",
            error="Google sign-in failed. Please try again.",
        )
        if terminal_response is not None:
            return terminal_response
        raise HTTPException(status_code=400, detail="Google sign-in failed. Please try again.") from None
    finally:
        # A database failure can prevent durable tracking itself. The normal
        # path above always queues first under the subject lock; this fallback
        # is reserved for failure to enter that lock at all. Reacquire subject
        # ownership before touching a stored connection whenever possible.
        if unpersisted_google_tokens is not None and unpersisted_google_subject_hash is not None:
            try:
                with exclusive_google_subject_lock(
                    str(settings.database_path),
                    subject_hash=unpersisted_google_subject_hash,
                ):
                    _cleanup_unpersisted_callback_grant(
                        database_url=str(settings.database_path),
                        subject_hash=unpersisted_google_subject_hash,
                        tokens=unpersisted_google_tokens,
                        user_id=callback_user_id,
                    )
            except AdvisoryLockUnavailable:
                # Contention is not permission to escape subject ordering. A
                # critical worker will take the same lock before provider I/O.
                # If even enqueueing fails, leave this credential unresolved;
                # revoking here could invalidate the callback holding the lock.
                _queue_callback_grant_cleanup(
                    subject_hash=unpersisted_google_subject_hash,
                    tokens=unpersisted_google_tokens,
                )
            except Exception:
                # The lock session itself may be unavailable. Try to secure a
                # durable job first; direct revocation is the last resort only
                # when that database write also cannot be completed.
                _queue_callback_grant_cleanup_or_revoke(
                    subject_hash=unpersisted_google_subject_hash,
                    tokens=unpersisted_google_tokens,
                )
            unpersisted_google_tokens = None

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
