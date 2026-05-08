from __future__ import annotations

"""Google OAuth, Gmail thread actions, and Calendar ingestion helpers."""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from email.message import EmailMessage
from email.utils import getaddresses
import hashlib
import html
import json
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.config import BACKEND_DIR, Settings
from app.db.models import StoredGmailHistoryEvent, StoredGmailMessageSnapshot, StoredSourceRecord
from app.db.repository import (
    append_trace_record,
    clear_all_data,
    get_gmail_sync_state,
    get_source_record_count,
    initialize_database,
    list_existing_source_record_ids,
    upsert_gmail_history_events,
    upsert_gmail_message_snapshots,
    upsert_gmail_sync_state,
    upsert_source_records,
    utc_now_iso,
)
from app.schemas.domain import DashboardProfile, GoogleAuthState, SourceRecord


GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_WRITE_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
GOOGLE_SCOPES = [GMAIL_SCOPE, GMAIL_WRITE_SCOPE, CALENDAR_SCOPE]
TOKEN_FILE_PATH = BACKEND_DIR / ".google-oauth.json"
OAUTH_SESSION_FILE_PATH = BACKEND_DIR / ".google-oauth-session.json"
ACCOUNT_PROFILE_FILE_PATH = BACKEND_DIR / ".google-account.json"
DEV_USER_ID = "google-dev-user"
GMAIL_PAGE_SIZE = 100
GMAIL_LOOKBACK_DAYS = 90
CALENDAR_MAX_RESULTS = 20
CALENDAR_WINDOW_DAYS = 7
GMAIL_SYNC_SCOPES = {"full", "recent"}
GMAIL_INBOX_LABEL = "INBOX"
GMAIL_UNREAD_LABEL = "UNREAD"
HTML_TAG_PATTERN = re.compile(r"(?is)<[^>]+>")
GmailSyncProgressCallback = Callable[[str, int, int | None], None]


@dataclass
class GmailMessageIdPage:
    """One bounded page of Gmail message ids from messages.list."""

    message_ids: list[str]
    total_count: int | None


@dataclass
class GmailBatchResult:
    """Persisted result for one bounded Gmail hydration batch."""

    records: list[SourceRecord]
    threads_fetched: int
    latest_history_id: str | None
    new_count: int
    failed_message_ids: list[str]


def _gmail_debug_enabled() -> bool:
    """Gate verbose Gmail API logging behind an env flag for local debugging."""
    return os.getenv("GMAIL_DEBUG_LOGS", "").strip().lower() in {"1", "true", "yes", "on"}


def _log_gmail_debug(label: str, payload: dict[str, object]) -> None:
    """Print Gmail API inputs and raw outputs to the backend terminal."""
    if not _gmail_debug_enabled():
        return

    print(
        json.dumps(
            {
                "gmail_debug": True,
                "label": label,
                "payload": payload,
            },
            ensure_ascii=True,
            indent=2,
        )
    )


def _log_gmail_error(label: str, payload: dict[str, object]) -> None:
    """Print sync errors even when verbose Gmail debugging is disabled."""
    print(
        json.dumps(
            {
                "gmail_error": True,
                "label": label,
                "payload": payload,
            },
            ensure_ascii=True,
            indent=2,
        )
    )


def get_google_auth_url(settings: Settings, redirect_to: str | None = None) -> str:
    """Build the Google authorization URL for local OAuth setup."""
    flow = create_flow(settings)
    authorization_url, state = flow.authorization_url(access_type="offline", prompt="consent")
    session = {
        "state": state,
        "code_verifier": flow.code_verifier,
    }

    if redirect_to is not None:
        session["redirect_to"] = redirect_to

    save_oauth_session(session)
    return authorization_url


def handle_google_callback(settings: Settings, code: str, state: str | None = None) -> str:
    """Exchange the OAuth code, persist tokens, and return the final client redirect."""
    session = load_oauth_session(state)
    if session is None:
        raise RuntimeError("Missing OAuth session. Start again from /auth/google.")

    expected_state = session.get("state")
    code_verifier = session.get("code_verifier")
    redirect_to = session.get("redirect_to")

    if state is not None and expected_state and state != expected_state:
        clear_oauth_session(state)
        raise RuntimeError("OAuth state mismatch. Start again from /auth/google.")

    if not isinstance(code_verifier, str) or not code_verifier.strip():
        clear_oauth_session(state)
        raise RuntimeError("Missing OAuth code verifier. Start again from /auth/google.")

    flow = create_flow(settings)
    flow.code_verifier = code_verifier
    flow.fetch_token(code=code)
    credentials = flow.credentials
    save_tokens(
        {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes,
        }
    )
    profile = fetch_google_account_profile_from_credentials(credentials)
    if profile is not None:
        _reset_local_data_if_account_changed(settings, profile)
        save_google_account_profile(profile)
    clear_oauth_session(state)

    if isinstance(redirect_to, str) and redirect_to == settings.mobile_redirect_uri:
        return redirect_to

    return f"{settings.cors_origin}/post-login"


def fetch_google_source_records(
    settings: Settings,
    *,
    progress_callback: GmailSyncProgressCallback | None = None,
    collect_records: bool = True,
) -> list[SourceRecord]:
    """Sync Gmail incrementally, fetch calendar updates, and return changed records."""
    credentials = create_authorized_credentials(settings)

    if credentials is None:
        return []

    initialize_database(str(settings.database_path))
    gmail_service = build("gmail", "v1", credentials=credentials)
    calendar_service = build("calendar", "v3", credentials=credentials)
    gmail_records = sync_gmail_source_records(
        settings,
        gmail_service,
        progress_callback=progress_callback,
        collect_records=collect_records,
    )
    calendar_records = fetch_upcoming_calendar_records(calendar_service)

    if calendar_records:
        persist_source_records(settings, calendar_records)

    if not collect_records:
        return calendar_records

    return sorted(gmail_records + calendar_records, key=lambda record: record.received_at, reverse=True)


def fetch_raw_gmail_source_records(settings: Settings) -> list[SourceRecord]:
    """Fetch a full live Gmail snapshot for debugging, without persisting sync state."""
    credentials = create_authorized_credentials(settings)

    if credentials is None:
        return []

    gmail_service = build("gmail", "v1", credentials=credentials)
    message_ids = fetch_all_gmail_message_ids(
        gmail_service,
        scope=resolve_gmail_sync_scope(settings.gmail_sync_scope),
        recent_days=settings.gmail_recent_days,
    )
    messages, _threads_fetched, _history_id, _failed_message_ids = fetch_thread_messages_for_message_ids(
        gmail_service,
        message_ids,
    )
    return normalize_gmail_messages(
        messages,
        scope=resolve_gmail_sync_scope(settings.gmail_sync_scope),
        recent_days=settings.gmail_recent_days,
    )


def fetch_raw_gmail_api_messages(settings: Settings) -> list[dict[str, object]]:
    """Fetch full raw Gmail API message payloads for debugging and copy/paste inspection."""
    credentials = create_authorized_credentials(settings)

    if credentials is None:
        return []

    gmail_service = build("gmail", "v1", credentials=credentials)
    message_ids = fetch_all_gmail_message_ids(
        gmail_service,
        scope=resolve_gmail_sync_scope(settings.gmail_sync_scope),
        recent_days=settings.gmail_recent_days,
    )
    messages, _threads_fetched, _history_id, _failed_message_ids = fetch_thread_messages_for_message_ids(
        gmail_service,
        message_ids,
    )
    return messages


def fetch_clean_gmail_api_messages(settings: Settings) -> list[dict[str, object]]:
    """Fetch Gmail API messages in a readable debugging shape without MIME/base64 noise."""
    return [clean_gmail_api_message(message) for message in fetch_raw_gmail_api_messages(settings)]


def archive_gmail_thread(settings: Settings, thread_id: str) -> dict[str, object]:
    """Archive one Gmail thread by removing its INBOX label."""
    return modify_gmail_thread_labels(
        settings,
        thread_id,
        remove_label_ids=[GMAIL_INBOX_LABEL],
    )


def unarchive_gmail_thread(settings: Settings, thread_id: str) -> dict[str, object]:
    """Unarchive one Gmail thread by restoring its INBOX label."""
    return modify_gmail_thread_labels(
        settings,
        thread_id,
        add_label_ids=[GMAIL_INBOX_LABEL],
    )


def mark_gmail_thread_read(settings: Settings, thread_id: str) -> dict[str, object]:
    """Mark one Gmail thread read from an explicit user action."""
    return modify_gmail_thread_labels(
        settings,
        thread_id,
        remove_label_ids=[GMAIL_UNREAD_LABEL],
    )


def create_gmail_draft(
    settings: Settings,
    *,
    to: str,
    cc: str | None = None,
    bcc: str | None = None,
    subject: str,
    body: str,
    thread_id: str | None = None,
) -> dict[str, object]:
    """Create one Gmail draft from an explicit user action."""
    gmail_service = create_gmail_service(settings)
    message_body = {"raw": build_raw_email(to=to, cc=cc, bcc=bcc, subject=subject, body=body)}
    if thread_id:
        message_body["threadId"] = thread_id
    return gmail_service.users().drafts().create(userId="me", body={"message": message_body}).execute()


def update_gmail_draft(
    settings: Settings,
    gmail_draft_id: str,
    *,
    to: str,
    cc: str | None = None,
    bcc: str | None = None,
    subject: str,
    body: str,
    thread_id: str | None = None,
) -> dict[str, object]:
    """Replace one existing Gmail draft from an explicit user action."""
    gmail_service = create_gmail_service(settings)
    message_body = {"raw": build_raw_email(to=to, cc=cc, bcc=bcc, subject=subject, body=body)}
    if thread_id:
        message_body["threadId"] = thread_id
    return gmail_service.users().drafts().update(
        userId="me",
        id=gmail_draft_id,
        body={"id": gmail_draft_id, "message": message_body},
    ).execute()


def send_gmail_draft(settings: Settings, gmail_draft_id: str) -> dict[str, object]:
    """Send one existing Gmail draft from an explicit user action."""
    gmail_service = create_gmail_service(settings)
    return gmail_service.users().drafts().send(userId="me", body={"id": gmail_draft_id}).execute()


def delete_gmail_draft(settings: Settings, gmail_draft_id: str) -> None:
    """Delete one existing Gmail draft from an explicit user action."""
    gmail_service = create_gmail_service(settings)
    gmail_service.users().drafts().delete(userId="me", id=gmail_draft_id).execute()


def persist_source_records(settings: Settings, records: list[SourceRecord]) -> None:
    """Persist normalized Google records into the local SQLite store."""
    initialize_database(str(settings.database_path))
    upsert_source_records(
        str(settings.database_path),
        [
            StoredSourceRecord(
                id=record.id,
                source=record.source,
                thread_id=record.thread_id or None,
                subject=string_value(record.raw_payload, "subject"),
                sender=string_value(record.raw_payload, "from") or string_value(record.raw_payload, "sender"),
                timestamp=record.received_at,
                raw_payload=record.raw_payload,
                created_at=utc_now_iso(),
            )
            for record in records
        ],
    )
    for record in records:
        normalized_output = {
            "id": record.id,
            "source": record.source,
            "thread_id": record.thread_id,
            "received_at": record.received_at,
        }
        append_trace_record(
            str(settings.database_path),
            stage="ingestion",
            user_id=record.user_id,
            source_record_id=record.id,
            input={"raw_payload": record.raw_payload},
            output=normalized_output,
        )
        append_trace_record(
            str(settings.database_path),
            stage="normalization",
            user_id=record.user_id,
            source_record_id=record.id,
            input={"raw_payload": record.raw_payload},
            output={
                **normalized_output,
                "subject": string_value(record.raw_payload, "subject"),
                "sender": string_value(record.raw_payload, "from") or string_value(record.raw_payload, "sender"),
            },
        )


def has_stored_google_tokens() -> bool:
    """Report whether local OAuth credentials have already been captured."""
    return TOKEN_FILE_PATH.exists()


def get_google_auth_state(settings: Settings) -> GoogleAuthState:
    """Return whether Google OAuth is usable and currently connected."""
    if not settings.google_configured:
        return GoogleAuthState(available=False, connected=False, connect_url=None)

    credentials = create_authorized_credentials(settings)
    return GoogleAuthState(
        available=True,
        connected=credentials is not None,
        connect_url=None if credentials is not None else f"{settings.backend_origin}/auth/google",
    )


def create_flow(settings: Settings) -> Flow:
    """Construct the OAuth flow with the configured Google client credentials."""
    if not settings.google_configured:
        raise RuntimeError("Google OAuth is not configured")

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=GOOGLE_SCOPES,
        redirect_uri=settings.google_redirect_uri,
    )
    return flow


def create_authorized_credentials(settings: Settings) -> Credentials | None:
    """Load and refresh stored OAuth credentials when available."""
    if not TOKEN_FILE_PATH.exists() or not settings.google_configured:
        return None

    try:
        tokens = json.loads(TOKEN_FILE_PATH.read_text())
    except json.JSONDecodeError:
        clear_google_auth_state()
        return None

    normalized_tokens = {
        **tokens,
        "client_id": tokens.get("client_id") or settings.google_client_id,
        "client_secret": tokens.get("client_secret") or settings.google_client_secret,
        "token_uri": tokens.get("token_uri") or "https://oauth2.googleapis.com/token",
        "scopes": tokens.get("scopes") or GOOGLE_SCOPES,
    }
    if normalized_tokens != tokens:
        save_tokens(normalized_tokens)

    try:
        credentials = Credentials.from_authorized_user_info(normalized_tokens, GOOGLE_SCOPES)
    except Exception:
        clear_google_auth_state()
        return None

    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except Exception:
            clear_google_auth_state()
            return None
        save_tokens(
            {
                "token": credentials.token,
                "refresh_token": credentials.refresh_token,
                "token_uri": credentials.token_uri,
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
                "scopes": credentials.scopes,
            }
        )

    return credentials


def save_tokens(tokens: dict[str, object]) -> None:
    """Write OAuth tokens to the local backend credential file."""
    TOKEN_FILE_PATH.write_text(json.dumps(tokens, indent=2))


def clear_google_auth_state() -> None:
    """Remove persisted local Google auth artifacts when credentials are unusable."""
    for file_path in (TOKEN_FILE_PATH, ACCOUNT_PROFILE_FILE_PATH, OAUTH_SESSION_FILE_PATH):
        if file_path.exists():
            file_path.unlink()


def fetch_google_account_profile(settings: Settings) -> DashboardProfile | None:
    """Resolve the connected Google account profile for the dashboard."""
    credentials = create_authorized_credentials(settings)

    if credentials is None:
        return load_google_account_profile()

    profile = fetch_google_account_profile_from_credentials(credentials)
    if profile is not None:
        save_google_account_profile(profile)
        return profile

    return load_google_account_profile()


def fetch_google_account_profile_from_credentials(credentials: Credentials) -> DashboardProfile | None:
    """Fetch the mailbox profile using the current authorized credentials."""
    try:
        gmail_service = build("gmail", "v1", credentials=credentials)
        payload = gmail_service.users().getProfile(userId="me").execute()
    except HttpError:
        return None
    except Exception:
        return None

    email = str(payload.get("emailAddress") or "").strip() or None
    return DashboardProfile(email=email)


def save_google_account_profile(profile: DashboardProfile) -> None:
    """Persist the resolved Google mailbox profile for local reuse."""
    ACCOUNT_PROFILE_FILE_PATH.write_text(json.dumps(profile.model_dump(), indent=2))


def load_google_account_profile() -> DashboardProfile | None:
    """Load the last resolved Google mailbox profile from disk."""
    if not ACCOUNT_PROFILE_FILE_PATH.exists():
        return None

    try:
        return DashboardProfile.model_validate(json.loads(ACCOUNT_PROFILE_FILE_PATH.read_text()))
    except Exception:
        return None


def _reset_local_data_if_account_changed(settings: Settings, profile: DashboardProfile) -> None:
    """Clear persisted mailbox state when the connected Google account changes."""
    previous_profile = load_google_account_profile()

    if previous_profile is None or previous_profile.email is None or profile.email is None:
        return

    if previous_profile.email.strip().lower() == profile.email.strip().lower():
        return

    initialize_database(str(settings.database_path))
    clear_all_data(str(settings.database_path))


def save_oauth_session(session: dict[str, object]) -> None:
    """Persist the temporary OAuth PKCE session between redirect and callback."""
    state = session.get("state")
    if not isinstance(state, str) or not state.strip():
        OAUTH_SESSION_FILE_PATH.write_text(json.dumps(session, indent=2))
        return

    sessions = load_oauth_sessions()
    sessions[state] = session
    OAUTH_SESSION_FILE_PATH.write_text(json.dumps({"sessions": sessions}, indent=2))


def load_oauth_session(state: str | None = None) -> dict[str, object] | None:
    """Load the temporary OAuth session used to complete the PKCE token exchange."""
    if not OAUTH_SESSION_FILE_PATH.exists():
        return None

    payload = json.loads(OAUTH_SESSION_FILE_PATH.read_text())
    sessions = payload.get("sessions")
    if isinstance(sessions, dict):
        if isinstance(state, str) and state in sessions and isinstance(sessions[state], dict):
            return sessions[state]
        if state is None and len(sessions) == 1:
            only_session = next(iter(sessions.values()))
            return only_session if isinstance(only_session, dict) else None
        return None

    expected_state = payload.get("state")
    if state is None or state == expected_state:
        return payload
    return None


def load_oauth_sessions() -> dict[str, dict[str, object]]:
    """Load all pending OAuth sessions keyed by state."""
    if not OAUTH_SESSION_FILE_PATH.exists():
        return {}

    try:
        payload = json.loads(OAUTH_SESSION_FILE_PATH.read_text())
    except json.JSONDecodeError:
        return {}

    sessions = payload.get("sessions")
    if isinstance(sessions, dict):
        return {str(key): value for key, value in sessions.items() if isinstance(value, dict)}

    state = payload.get("state")
    if isinstance(state, str) and state.strip():
        return {state: payload}
    return {}


def clear_oauth_session(state: str | None = None) -> None:
    """Remove OAuth session state after success or mismatch."""
    if OAUTH_SESSION_FILE_PATH.exists():
        if state is None:
            OAUTH_SESSION_FILE_PATH.unlink()
            return

        sessions = load_oauth_sessions()
        sessions.pop(state, None)
        if sessions:
            OAUTH_SESSION_FILE_PATH.write_text(json.dumps({"sessions": sessions}, indent=2))
            return

        OAUTH_SESSION_FILE_PATH.unlink()


def sync_gmail_source_records(
    settings: Settings,
    gmail_service,
    *,
    progress_callback: GmailSyncProgressCallback | None = None,
    collect_records: bool = True,
) -> list[SourceRecord]:
    """Run resumable Gmail sync with full initial load and history-based incremental updates."""
    database_path = str(settings.database_path)
    sync_state = get_gmail_sync_state(database_path, DEV_USER_ID)
    used_full_sync = sync_state is None or not sync_state.last_history_id
    sync_scope = resolve_gmail_sync_scope(settings.gmail_sync_scope)
    history_events: list[StoredGmailHistoryEvent] = []
    records_by_id: dict[str, SourceRecord] = {}
    imported_count = 0
    total_count: int | None = None
    threads_fetched = 0
    new_count = 0
    latest_history_id: str | None = None
    mailbox_history_id: str | None = None
    fetched_thread_ids: set[str] = set()
    failed_message_ids: set[str] = set()

    def report(stage: str) -> None:
        if progress_callback is not None:
            progress_callback(stage, imported_count, total_count)

    if used_full_sync:
        report("gmail_listing")
        for page in iter_gmail_message_id_pages(gmail_service, scope=sync_scope, recent_days=settings.gmail_recent_days):
            total_count = page.total_count if page.total_count is not None else total_count
            if not page.message_ids:
                report("gmail_fetching")
                continue

            report("gmail_fetching")
            batch = persist_gmail_message_id_batch(
                settings,
                gmail_service,
                page.message_ids,
                scope=sync_scope,
                recent_days=settings.gmail_recent_days,
                fetched_thread_ids=fetched_thread_ids,
            )
            imported_count += len(set(page.message_ids))
            threads_fetched += batch.threads_fetched
            new_count += batch.new_count
            latest_history_id = max_history_id(latest_history_id, batch.latest_history_id)
            failed_message_ids.update(batch.failed_message_ids)
            if collect_records:
                records_by_id.update({record.id: record for record in batch.records})
            report("gmail_persisted")
    else:
        message_ids, mailbox_history_id, needs_full_sync, history_events = fetch_incremental_gmail_changes(
            gmail_service,
            sync_state.last_history_id,
        )
        if history_events:
            upsert_gmail_history_events(database_path, history_events)
        deleted_message_ids = {
            event.message_id
            for event in history_events
            if event.event_type == "messagesDeleted"
        }
        if needs_full_sync:
            used_full_sync = True
            message_ids = []
            mailbox_history_id = None
            report("gmail_listing")
            for page in iter_gmail_message_id_pages(gmail_service, scope=sync_scope, recent_days=settings.gmail_recent_days):
                total_count = page.total_count if page.total_count is not None else total_count
                if not page.message_ids:
                    report("gmail_fetching")
                    continue

                report("gmail_fetching")
                batch = persist_gmail_message_id_batch(
                    settings,
                    gmail_service,
                    page.message_ids,
                    scope=sync_scope,
                    recent_days=settings.gmail_recent_days,
                    fetched_thread_ids=fetched_thread_ids,
                )
                imported_count += len(set(page.message_ids))
                threads_fetched += batch.threads_fetched
                new_count += batch.new_count
                latest_history_id = max_history_id(latest_history_id, batch.latest_history_id)
                failed_message_ids.update(batch.failed_message_ids)
                if collect_records:
                    records_by_id.update({record.id: record for record in batch.records})
                report("gmail_persisted")
        else:
            total_count = len(message_ids)
            for batch_ids in chunked(message_ids, GMAIL_PAGE_SIZE):
                report("gmail_fetching")
                batch = persist_gmail_message_id_batch(
                    settings,
                    gmail_service,
                    batch_ids,
                    scope=sync_scope,
                    recent_days=settings.gmail_recent_days,
                    fetched_thread_ids=fetched_thread_ids,
                    deleted_message_ids=deleted_message_ids,
                )
                imported_count += len(set(batch_ids))
                threads_fetched += batch.threads_fetched
                new_count += batch.new_count
                latest_history_id = max_history_id(latest_history_id, batch.latest_history_id)
                failed_message_ids.update(batch.failed_message_ids)
                if collect_records:
                    records_by_id.update({record.id: record for record in batch.records})
                report("gmail_persisted")

    resolved_history_id = mailbox_history_id or latest_history_id or (sync_state.last_history_id if sync_state else None)
    if failed_message_ids:
        _log_gmail_error(
            "gmail_sync_incomplete",
            {
                "failedMessageIds": sorted(failed_message_ids),
                "lastHistoryIdPreserved": sync_state.last_history_id if sync_state else None,
                "resolvedHistoryIdNotCommitted": resolved_history_id,
            },
        )
    else:
        upsert_gmail_sync_state(
            database_path,
            user_id=DEV_USER_ID,
            last_history_id=resolved_history_id,
            last_full_sync_at=utc_now_iso() if used_full_sync else (sync_state.last_full_sync_at if sync_state else None),
        )

    total_source_records = get_source_record_count(database_path)
    records = sorted(records_by_id.values(), key=lambda record: record.received_at, reverse=True)
    fetched_count = imported_count if not collect_records else len(records_by_id)
    if total_source_records < fetched_count:
        print(
            "GMAIL SYNC INVARIANT VIOLATION",
            {
                "total_source_records": total_source_records,
                "total_gmail_messages_fetched": fetched_count,
            },
        )

    print(
        {
            "messages_fetched": fetched_count,
            "new_messages": new_count,
            "threads_fetched": threads_fetched,
        }
    )

    return records


def fetch_all_gmail_message_ids(gmail_service, *, scope: str, recent_days: int) -> list[str]:
    """List Gmail message ids for either full-mailbox or recent-window sync."""
    ids: list[str] = []

    for page in iter_gmail_message_id_pages(gmail_service, scope=scope, recent_days=recent_days):
        ids.extend(page.message_ids)

    return ids


def iter_gmail_message_id_pages(
    gmail_service,
    *,
    scope: str,
    recent_days: int,
) -> Iterable[GmailMessageIdPage]:
    """Yield Gmail message ids one API page at a time."""
    page_token: str | None = None
    query = None if scope == "full" else f"newer_than:{recent_days}d"

    while True:
        request_payload = {
            "userId": "me",
            "maxResults": GMAIL_PAGE_SIZE,
            "labelIds": None,
            "q": query,
            "pageToken": page_token,
        }
        listed = (
            gmail_service.users()
            .messages()
            .list(
                userId="me",
                maxResults=GMAIL_PAGE_SIZE,
                q=query,
                pageToken=page_token,
            )
            .execute()
        )
        _log_gmail_debug("gmail_list_response", {"request": request_payload, "response": listed})

        page_ids: list[str] = []
        for message in listed.get("messages", []):
            message_id = message.get("id")
            if message_id:
                page_ids.append(str(message_id))

        estimate = listed.get("resultSizeEstimate")
        total_count = int(estimate) if isinstance(estimate, int) else None
        yield GmailMessageIdPage(message_ids=page_ids, total_count=total_count)

        page_token = listed.get("nextPageToken")
        if not page_token:
            break


def persist_gmail_message_id_batch(
    settings: Settings,
    gmail_service,
    message_ids: list[str],
    *,
    scope: str,
    recent_days: int,
    fetched_thread_ids: set[str] | None = None,
    deleted_message_ids: set[str] | None = None,
) -> GmailBatchResult:
    """Hydrate, normalize, and commit one bounded Gmail message-id batch."""
    if not message_ids:
        return GmailBatchResult(records=[], threads_fetched=0, latest_history_id=None, new_count=0, failed_message_ids=[])

    database_path = str(settings.database_path)
    messages, threads_fetched, latest_history_id, failed_message_ids = fetch_thread_messages_for_message_ids(
        gmail_service,
        message_ids,
        fetched_thread_ids=fetched_thread_ids,
    )
    retryable_failed_message_ids = sorted(set(failed_message_ids) - (deleted_message_ids or set()))
    if messages:
        upsert_gmail_message_snapshots(
            database_path,
            [gmail_message_snapshot_from_payload(message) for message in messages],
        )
    records = normalize_gmail_messages(messages, scope=scope, recent_days=recent_days)
    existing_ids = list_existing_source_record_ids(database_path, (record.id for record in records))
    new_count = len([record for record in records if record.id not in existing_ids])

    if records:
        persist_source_records(settings, records)

    return GmailBatchResult(
        records=records,
        threads_fetched=threads_fetched,
        latest_history_id=latest_history_id,
        new_count=new_count,
        failed_message_ids=retryable_failed_message_ids,
    )


def chunked(values: list[str], size: int) -> Iterable[list[str]]:
    """Yield bounded chunks from a small incremental id set."""
    for index in range(0, len(values), size):
        yield values[index : index + size]


def fetch_incremental_gmail_message_ids(
    gmail_service,
    start_history_id: str,
) -> tuple[list[str], str | None, bool]:
    """List Gmail message ids changed since the stored history cursor."""
    message_ids, latest_history_id, needs_full_sync, _events = fetch_incremental_gmail_changes(
        gmail_service,
        start_history_id,
    )
    return message_ids, latest_history_id, needs_full_sync


def fetch_incremental_gmail_changes(
    gmail_service,
    start_history_id: str,
) -> tuple[list[str], str | None, bool, list[StoredGmailHistoryEvent]]:
    """List changed Gmail ids and preserve History API events before hydration."""
    ids: set[str] = set()
    events_by_id: dict[str, StoredGmailHistoryEvent] = {}
    page_token: str | None = None
    latest_history_id: str | None = None

    try:
        while True:
            listed = (
                gmail_service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=start_history_id,
                    pageToken=page_token,
                )
                .execute()
            )
            _log_gmail_debug(
                "gmail_history_response",
                {
                    "request": {
                        "userId": "me",
                        "startHistoryId": start_history_id,
                        "pageToken": page_token,
                    },
                    "response": listed,
                },
            )
            latest_history_id = str(listed.get("historyId")) if listed.get("historyId") is not None else latest_history_id

            for history_entry in listed.get("history", []):
                latest_history_id = max_history_id(
                    latest_history_id,
                    str(history_entry.get("id")) if history_entry.get("id") is not None else None,
                )
                ids.update(extract_history_message_ids(history_entry))
                for event in extract_gmail_history_events(history_entry):
                    events_by_id[event.id] = event

            page_token = listed.get("nextPageToken")
            if not page_token:
                break
    except HttpError as error:
        if getattr(error, "status_code", None) == 404 or getattr(getattr(error, "resp", None), "status", None) == 404:
            _log_gmail_error(
                "gmail_history_reset_required",
                {
                    "startHistoryId": start_history_id,
                    "error": str(error),
                },
            )
            return [], None, True, []
        raise

    return sorted(ids), latest_history_id, False, list(events_by_id.values())


def extract_history_message_ids(history_entry: dict[str, object]) -> set[str]:
    """Collect every touched Gmail message id from one History API record."""
    ids: set[str] = set()

    for key in ("messages", "messagesAdded", "messagesDeleted", "labelsAdded", "labelsRemoved"):
        values = history_entry.get(key)
        if not isinstance(values, list):
            continue

        for value in values:
            if isinstance(value, dict) and isinstance(value.get("message"), dict):
                message_id = value["message"].get("id")
            elif isinstance(value, dict):
                message_id = value.get("id")
            else:
                message_id = None

            if message_id:
                ids.add(str(message_id))

    return ids


def extract_gmail_history_events(history_entry: dict[str, object]) -> list[StoredGmailHistoryEvent]:
    """Project Gmail History API records into durable local ledger events."""
    events: list[StoredGmailHistoryEvent] = []
    history_id = str(history_entry.get("id") or "")
    created_at = utc_now_iso()

    for event_type in ("messagesAdded", "messagesDeleted", "labelsAdded", "labelsRemoved"):
        values = history_entry.get(event_type)
        if not isinstance(values, list):
            continue

        for index, value in enumerate(values):
            if not isinstance(value, dict):
                continue
            message = value.get("message") if isinstance(value.get("message"), dict) else value
            if not isinstance(message, dict):
                continue
            message_id = message.get("id")
            if not message_id:
                continue
            label_ids = value.get("labelIds") if isinstance(value.get("labelIds"), list) else None
            if label_ids is None:
                label_ids = message.get("labelIds") if isinstance(message.get("labelIds"), list) else []

            normalized_label_ids = [str(label_id) for label_id in label_ids]
            event = StoredGmailHistoryEvent(
                id=gmail_history_event_id(
                    history_id=history_id,
                    event_type=event_type,
                    message_id=str(message_id),
                    label_ids=normalized_label_ids,
                    index=index,
                ),
                user_id=DEV_USER_ID,
                history_id=history_id,
                event_type=event_type,
                message_id=str(message_id),
                thread_id=str(message.get("threadId")) if message.get("threadId") is not None else None,
                label_ids=normalized_label_ids,
                message_payload=message,
                created_at=created_at,
            )
            events.append(event)

    return events


def gmail_history_event_id(
    *,
    history_id: str,
    event_type: str,
    message_id: str,
    label_ids: list[str],
    index: int,
) -> str:
    """Build a deterministic event id so repeated history pages are idempotent."""
    payload = {
        "history_id": history_id,
        "event_type": event_type,
        "message_id": message_id,
        "label_ids": label_ids,
        "index": index,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return f"gmail-history:{digest}"


def fetch_thread_messages_for_message_ids(
    gmail_service,
    message_ids: list[str],
    *,
    fetched_thread_ids: set[str] | None = None,
) -> tuple[list[dict[str, object]], int, str | None, list[str]]:
    """Fetch each changed message in full, then ingest the entire thread for full context."""
    thread_messages: dict[str, dict[str, object]] = {}
    fetched_threads = set() if fetched_thread_ids is None else fetched_thread_ids
    fetched_thread_count = 0
    failed_message_ids: set[str] = set()
    latest_history_id: str | None = None

    for message_id in message_ids:
        try:
            full_message = (
                gmail_service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            _log_gmail_debug(
                "gmail_message_full",
                {
                    "messageId": str(message_id),
                    "threadId": str(full_message.get("threadId") or ""),
                    "response": full_message,
                },
            )
        except Exception as error:  # pragma: no cover - exercised via behavior, not exception type
            _log_gmail_error(
                "gmail_message_fetch_failed",
                {
                    "messageId": message_id,
                    "error": str(error),
                },
            )
            failed_message_ids.add(str(message_id))
            continue

        latest_history_id = max_history_id(
            latest_history_id,
            str(full_message.get("historyId")) if full_message.get("historyId") is not None else None,
        )
        full_message_id = full_message.get("id")
        if full_message_id:
            thread_messages[str(full_message_id)] = full_message
            failed_message_ids.discard(str(full_message_id))

        thread_id = full_message.get("threadId")
        if not thread_id or str(thread_id) in fetched_threads:
            continue

        try:
            thread = (
                gmail_service.users()
                .threads()
                .get(userId="me", id=str(thread_id), format="full")
                .execute()
            )
            _log_gmail_debug(
                "gmail_thread_full",
                {
                    "threadId": str(thread_id),
                    "response": thread,
                },
            )
            fetched_threads.add(str(thread_id))
            fetched_thread_count += 1
        except Exception as error:  # pragma: no cover - exercised via behavior, not exception type
            _log_gmail_error(
                "gmail_thread_fetch_failed",
                {
                    "threadId": str(thread_id),
                    "messageId": message_id,
                    "error": str(error),
                },
            )
            continue

        for thread_message in thread.get("messages", []):
            if not isinstance(thread_message, dict):
                continue
            latest_history_id = max_history_id(
                latest_history_id,
                str(thread_message.get("historyId")) if thread_message.get("historyId") is not None else None,
            )
            thread_message_id = thread_message.get("id")
            if thread_message_id:
                thread_messages[str(thread_message_id)] = thread_message
                failed_message_ids.discard(str(thread_message_id))

    return list(thread_messages.values()), fetched_thread_count, latest_history_id, sorted(failed_message_ids)


def normalize_gmail_messages(
    messages: list[dict[str, object]],
    *,
    scope: str = "full",
    recent_days: int = GMAIL_LOOKBACK_DAYS,
) -> list[SourceRecord]:
    """Normalize Gmail API payloads into source records for the configured sync scope."""
    records_by_id: dict[str, SourceRecord] = {}
    cutoff_iso = get_gmail_cutoff_iso(recent_days) if scope == "recent" else None

    for message in messages:
        record = to_gmail_source_record(message)
        if record is None:
            continue
        if cutoff_iso is not None and record.received_at < cutoff_iso:
            continue
        records_by_id[record.id] = record

    return sorted(records_by_id.values(), key=lambda record: record.received_at, reverse=True)


def get_gmail_cutoff_iso(recent_days: int) -> str:
    """Return the earliest Gmail timestamp that should be kept in a recent sync window."""
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=recent_days)).isoformat()


def resolve_gmail_sync_scope(value: str) -> str:
    """Clamp configured Gmail sync scope to the supported values."""
    normalized = value.strip().lower()
    return normalized if normalized in GMAIL_SYNC_SCOPES else "full"


def max_history_id(left: str | None, right: str | None) -> str | None:
    """Keep the latest Gmail history cursor seen during the current sync cycle."""
    if left is None:
        return right
    if right is None:
        return left
    return left if int(left) >= int(right) else right


def gmail_message_snapshot_from_payload(message: dict[str, object]) -> StoredGmailMessageSnapshot:
    """Build a local Gmail ledger snapshot from a full Gmail API message payload."""
    now = utc_now_iso()
    label_ids = message.get("labelIds") if isinstance(message.get("labelIds"), list) else []
    return StoredGmailMessageSnapshot(
        user_id=DEV_USER_ID,
        message_id=str(message.get("id") or ""),
        thread_id=str(message.get("threadId")) if message.get("threadId") is not None else None,
        history_id=str(message.get("historyId")) if message.get("historyId") is not None else None,
        internal_date=to_gmail_internal_date_iso(message.get("internalDate")),
        label_ids=[str(label_id) for label_id in label_ids],
        raw_payload=message,
        fetch_status="fetched",
        tombstoned=False,
        tombstoned_at=None,
        last_fetched_at=now,
        created_at=now,
        updated_at=now,
    )


def fetch_upcoming_calendar_records(calendar_service) -> list[SourceRecord]:
    """Fetch near-term primary-calendar events within the configured window."""
    from datetime import datetime, timedelta, timezone

    time_min = datetime.now(timezone.utc).isoformat()
    time_max = (datetime.now(timezone.utc) + timedelta(days=CALENDAR_WINDOW_DAYS)).isoformat()
    listed = (
        calendar_service.events()
        .list(
            calendarId="primary",
            singleEvents=True,
            orderBy="startTime",
            timeMin=time_min,
            timeMax=time_max,
            maxResults=CALENDAR_MAX_RESULTS,
        )
        .execute()
    )

    records = []

    for event in listed.get("items", []):
        record = to_calendar_source_record(event)

        if record is not None:
            records.append(record)

    return records


def to_gmail_source_record(message: dict[str, object]) -> SourceRecord | None:
    """Normalize a Gmail API message payload into a SourceRecord."""
    payload = message.get("payload") or {}
    headers = payload.get("headers") or []
    subject = get_header(headers, "subject") or "(no subject)"
    sender = get_header(headers, "from") or ""
    recipients = get_header(headers, "to") or ""
    cc_recipients = get_header(headers, "cc") or ""
    bcc_recipients = get_header(headers, "bcc") or ""
    date_header = get_header(headers, "date")
    internal_date = message.get("internalDate")
    received_at = to_valid_iso(date_header) or to_gmail_internal_date_iso(internal_date)
    received_at = received_at or utc_now_iso()
    body = extract_gmail_body(payload).strip() or str(message.get("snippet") or "").strip()
    message_id = message.get("id")
    thread_id = message.get("threadId")
    participants = extract_participants(sender, recipients, cc_recipients, bcc_recipients)

    if not message_id or not thread_id:
        _log_gmail_debug(
            "gmail_message_dropped",
            {
                "reason": {
                    "missing_message_id": not bool(message_id),
                    "missing_thread_id": not bool(thread_id),
                },
                "messageId": str(message_id or ""),
                "threadId": str(thread_id or ""),
                "subject": subject,
                "sender": sender,
                "snippet": str(message.get("snippet") or ""),
                "raw": message,
            },
        )
        return None

    record = SourceRecord(
        id=str(message_id),
        user_id=DEV_USER_ID,
        source="gmail",
        thread_id=str(thread_id),
        raw_payload={
            "message_id": str(message_id),
            "subject": subject,
            "body": body,
            "from": sender,
            "to": recipients,
            "cc": cc_recipients,
            "bcc": bcc_recipients,
            "participants": participants,
            "received_at": received_at,
            "snippet": str(message.get("snippet") or ""),
            "label_ids": message.get("labelIds") if isinstance(message.get("labelIds"), list) else [],
            "history_id": str(message.get("historyId") or ""),
        },
        received_at=received_at,
    )
    _log_gmail_debug(
        "gmail_message_normalized",
        {
            "messageId": record.id,
            "threadId": record.thread_id,
            "received_at": record.received_at,
            "subject": subject,
            "sender": sender,
        },
    )
    return record


def to_calendar_source_record(event: dict[str, object]) -> SourceRecord | None:
    """Normalize a Calendar API event payload into a SourceRecord."""
    subject = str(event.get("summary") or "").strip()
    body = str(event.get("description") or "").strip()
    start = ((event.get("start") or {}).get("dateTime") if isinstance(event.get("start"), dict) else None) or (
        (event.get("start") or {}).get("date") if isinstance(event.get("start"), dict) else None
    )
    end = ((event.get("end") or {}).get("dateTime") if isinstance(event.get("end"), dict) else None) or (
        (event.get("end") or {}).get("date") if isinstance(event.get("end"), dict) else None
    )
    received_at = to_valid_iso(start) or to_valid_iso(event.get("updated"))
    event_id = event.get("id")

    if not event_id or not received_at or not subject:
        return None

    organizer = (
        ((event.get("organizer") or {}).get("email") if isinstance(event.get("organizer"), dict) else None)
        or ((event.get("creator") or {}).get("email") if isinstance(event.get("creator"), dict) else None)
        or "calendar@google.com"
    )
    attendees = [
        attendee.get("email")
        for attendee in (event.get("attendees") or [])
        if isinstance(attendee, dict) and attendee.get("email")
    ]

    return SourceRecord(
        id=str(event_id),
        user_id=DEV_USER_ID,
        source="calendar",
        thread_id=str(event_id),
        raw_payload={
            "subject": subject,
            "body": body,
            "from": organizer,
            "attendees": attendees,
            "all_day": not (isinstance(event.get("start"), dict) and event["start"].get("dateTime")),
            "start": start,
            "end": end,
        },
        received_at=received_at,
    )


def get_header(headers: list[object], name: str) -> str | None:
    """Read a named Gmail header from the raw Gmail API payload."""
    for header in headers:
        if isinstance(header, dict) and str(header.get("name", "")).lower() == name.lower():
            value = header.get("value")
            return str(value).strip() if isinstance(value, str) else None
    return None


def modify_gmail_thread_labels(
    settings: Settings,
    thread_id: str,
    *,
    add_label_ids: list[str] | None = None,
    remove_label_ids: list[str] | None = None,
) -> dict[str, object]:
    """Apply explicit Gmail label mutations to a single thread."""
    if not thread_id.strip():
        raise RuntimeError("Missing Gmail thread id")

    credentials = create_authorized_credentials(settings)
    if credentials is None:
        raise RuntimeError("Google account is not connected")

    gmail_service = build("gmail", "v1", credentials=credentials)
    request_body: dict[str, list[str]] = {}
    if add_label_ids:
        request_body["addLabelIds"] = add_label_ids
    if remove_label_ids:
        request_body["removeLabelIds"] = remove_label_ids

    _log_gmail_debug(
        "gmail_thread_modify_request",
        {
            "threadId": thread_id,
            "addLabelIds": add_label_ids or [],
            "removeLabelIds": remove_label_ids or [],
        },
    )

    try:
        response = (
            gmail_service.users()
            .threads()
            .modify(userId="me", id=thread_id, body=request_body)
            .execute()
        )
    except HttpError as error:
        _log_gmail_error(
            "gmail_thread_modify_failed",
            {
                "threadId": thread_id,
                "addLabelIds": add_label_ids or [],
                "removeLabelIds": remove_label_ids or [],
                "error": str(error),
            },
        )
        raise

    _log_gmail_debug(
        "gmail_thread_modify_response",
        {
            "threadId": thread_id,
            "response": response,
        },
    )
    return response


def create_gmail_service(settings: Settings):
    """Build an authorized Gmail service or fail with a user-facing runtime error."""
    credentials = create_authorized_credentials(settings)
    if credentials is None:
        raise RuntimeError("Google account is not connected")
    return build("gmail", "v1", credentials=credentials)


def build_raw_email(
    *,
    to: str,
    cc: str | None = None,
    bcc: str | None = None,
    subject: str,
    body: str,
) -> str:
    """Encode a simple UTF-8 text email for Gmail API draft/send endpoints."""
    message = EmailMessage()
    message["To"] = to
    if cc:
        message["Cc"] = cc
    if bcc:
        message["Bcc"] = bcc
    message["Subject"] = subject
    message.set_content(body)
    return urlsafe_b64encode(message.as_bytes()).decode("utf-8")


def extract_gmail_body(payload: dict[str, object]) -> str:
    """Walk the Gmail MIME payload tree until a readable text body is found."""
    if payload.get("mimeType") == "text/plain":
        body = payload.get("body") or {}

        if isinstance(body, dict) and body.get("data"):
            return decode_base64_url(str(body["data"]))

    for part in payload.get("parts") or []:
        if not isinstance(part, dict):
            continue
        extracted = extract_gmail_body(part)

        if extracted:
            return extracted

    body = payload.get("body") or {}

    if isinstance(body, dict) and body.get("data"):
        return decode_base64_url(str(body["data"]))

    return ""


def decode_base64_url(value: str) -> str:
    """Decode Gmail's URL-safe base64 body encoding."""
    padded = value + "=" * (-len(value) % 4)
    return urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8", errors="ignore")


def clean_gmail_api_message(message: dict[str, object]) -> dict[str, object]:
    """Project a raw Gmail API message into a compact debugging shape."""
    payload = message.get("payload") or {}
    headers = payload.get("headers") or []
    plain_body, html_body = extract_gmail_body_variants(payload)
    chosen_body = plain_body or html_to_text(html_body) or str(message.get("snippet") or "").strip()

    return {
        "id": str(message.get("id") or ""),
        "thread_id": str(message.get("threadId") or ""),
        "history_id": str(message.get("historyId") or ""),
        "label_ids": message.get("labelIds") if isinstance(message.get("labelIds"), list) else [],
        "internal_date": to_gmail_internal_date_iso(message.get("internalDate")),
        "snippet": str(message.get("snippet") or "").strip(),
        "subject": get_header(headers, "subject") or "",
        "from": get_header(headers, "from") or "",
        "to": get_header(headers, "to") or "",
        "cc": get_header(headers, "cc") or "",
        "bcc": get_header(headers, "bcc") or "",
        "date": get_header(headers, "date") or "",
        "mime_type": str(payload.get("mimeType") or ""),
        "body": {
            "text_plain": truncate_debug_text(plain_body),
            "text_html_as_text": truncate_debug_text(html_to_text(html_body)),
            "chosen": truncate_debug_text(chosen_body),
        },
        "parts": summarize_gmail_parts(payload),
    }


def extract_gmail_body_variants(payload: dict[str, object]) -> tuple[str, str]:
    """Walk the MIME tree and return best plain-text and html bodies."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict[str, object]) -> None:
        mime_type = str(part.get("mimeType") or "")
        body = part.get("body") or {}
        data = ""

        if isinstance(body, dict) and body.get("data"):
            data = decode_base64_url(str(body["data"]))

        if mime_type == "text/plain" and data.strip():
            plain_parts.append(data.strip())
        elif mime_type == "text/html" and data.strip():
            html_parts.append(data.strip())

        for child in part.get("parts") or []:
            if isinstance(child, dict):
                walk(child)

    walk(payload)

    return ("\n\n".join(plain_parts).strip(), "\n\n".join(html_parts).strip())


def summarize_gmail_parts(payload: dict[str, object]) -> list[dict[str, object]]:
    """Return readable metadata about Gmail MIME parts without raw data blobs."""
    summaries: list[dict[str, object]] = []

    def walk(part: dict[str, object]) -> None:
        body = part.get("body") or {}
        data = ""

        if isinstance(body, dict) and body.get("data"):
            data = decode_base64_url(str(body["data"]))

        headers = part.get("headers") or []
        summaries.append(
            {
                "part_id": str(part.get("partId") or ""),
                "mime_type": str(part.get("mimeType") or ""),
                "filename": str(part.get("filename") or ""),
                "size": body.get("size") if isinstance(body, dict) else None,
                "headers": {
                    name.lower(): value
                    for header in headers
                    if isinstance(header, dict)
                    and isinstance((name := header.get("name")), str)
                    and isinstance((value := header.get("value")), str)
                    and name.lower() in {"content-type", "content-transfer-encoding", "content-disposition"}
                },
                "text_preview": truncate_debug_text(html_to_text(data) if str(part.get("mimeType") or "") == "text/html" else data),
            }
        )

        for child in part.get("parts") or []:
            if isinstance(child, dict):
                walk(child)

    for part in payload.get("parts") or []:
        if isinstance(part, dict):
            walk(part)

    return summaries


def html_to_text(value: str) -> str:
    """Flatten simple HTML into readable text for debugging."""
    if not value:
        return ""

    text = value.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = re.sub(r"(?i)</p>|</div>|</li>|</tr>|</td>|</h[1-6]>", "\n", text)
    text = HTML_TAG_PATTERN.sub(" ", text)
    text = html.unescape(text)
    return " ".join(text.split())


def truncate_debug_text(value: str, limit: int = 1200) -> str:
    """Keep debug text readable in browser/JSON viewers."""
    text = value.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}...<truncated>"


def extract_participants(*address_fields: str) -> list[str]:
    """Normalize sender/recipient header fields into a compact participant list."""
    participants: list[str] = []
    seen: set[str] = set()

    for _display_name, address in getaddresses(address_fields):
        normalized = address.strip().lower()

        if not normalized or normalized in seen:
            continue

        seen.add(normalized)
        participants.append(normalized)

    return participants


def to_gmail_internal_date_iso(value: object) -> str | None:
    """Convert Gmail's millisecond internalDate value into a UTC ISO timestamp."""
    from datetime import datetime, timezone

    if value is None:
        return None

    try:
        milliseconds = int(str(value))
    except ValueError:
        return None

    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat()


def to_valid_iso(value) -> str | None:
    """Normalize the supported Gmail/Calendar date formats into UTC ISO timestamps."""
    from datetime import datetime, timezone

    if value is None:
        return None

    if isinstance(value, str) and value.isdigit():
        parsed = datetime.fromtimestamp(int(value), tz=timezone.utc)
        return parsed.isoformat()

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(str(value), "%a, %d %b %Y %H:%M:%S %z")
        except ValueError:
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc).isoformat()


def string_value(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None
