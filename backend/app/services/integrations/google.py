from __future__ import annotations

"""Google OAuth and read-only Gmail/Calendar ingestion helpers."""

from base64 import urlsafe_b64decode
import json

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.core.config import BACKEND_DIR, Settings
from app.db.models import StoredSourceRecord
from app.db.repository import append_trace_record, initialize_database, upsert_source_records, utc_now_iso
from app.schemas.domain import SourceRecord


GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
GOOGLE_SCOPES = [GMAIL_SCOPE, CALENDAR_SCOPE]
TOKEN_FILE_PATH = BACKEND_DIR / ".google-oauth.json"
DEV_USER_ID = "google-dev-user"
GMAIL_MAX_RESULTS = 100
CALENDAR_MAX_RESULTS = 20
CALENDAR_WINDOW_DAYS = 7


def get_google_auth_url(settings: Settings) -> str:
    """Build the Google authorization URL for local OAuth setup."""
    flow = create_flow(settings)
    authorization_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    return authorization_url


def handle_google_callback(settings: Settings, code: str) -> None:
    """Exchange the OAuth code for tokens and persist them locally."""
    flow = create_flow(settings)
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


def fetch_google_source_records(settings: Settings) -> list[SourceRecord]:
    """Fetch recent Gmail and Calendar data and normalize it into source records."""
    credentials = create_authorized_credentials(settings)

    if credentials is None:
        return []

    gmail_service = build("gmail", "v1", credentials=credentials)
    calendar_service = build("calendar", "v3", credentials=credentials)
    gmail_records = fetch_recent_gmail_records(gmail_service)
    calendar_records = fetch_upcoming_calendar_records(calendar_service)
    records = sorted(gmail_records + calendar_records, key=lambda record: record.received_at, reverse=True)
    persist_source_records(settings, records)
    return records


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

    tokens = json.loads(TOKEN_FILE_PATH.read_text())
    normalized_tokens = {
        **tokens,
        "client_id": tokens.get("client_id") or settings.google_client_id,
        "client_secret": tokens.get("client_secret") or settings.google_client_secret,
        "token_uri": tokens.get("token_uri") or "https://oauth2.googleapis.com/token",
        "scopes": tokens.get("scopes") or GOOGLE_SCOPES,
    }
    if normalized_tokens != tokens:
        save_tokens(normalized_tokens)

    credentials = Credentials.from_authorized_user_info(normalized_tokens, GOOGLE_SCOPES)

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
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


def fetch_recent_gmail_records(gmail_service) -> list[SourceRecord]:
    """Fetch recent inbox messages and normalize them one by one."""
    records: list[SourceRecord] = []
    page_token: str | None = None

    while len(records) < GMAIL_MAX_RESULTS:
        listed = (
            gmail_service.users()
            .messages()
            .list(
                userId="me",
                maxResults=min(100, GMAIL_MAX_RESULTS - len(records)),
                labelIds=["INBOX"],
                pageToken=page_token,
            )
            .execute()
        )

        messages = listed.get("messages", [])

        for message in messages:
            message_id = message.get("id")

            if not message_id:
                continue

            full = (
                gmail_service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            record = to_gmail_source_record(full)

            if record is not None:
                records.append(record)

        page_token = listed.get("nextPageToken")

        if not page_token or not messages:
            break

    return records


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
    subject = get_header(headers, "subject") or ""
    sender = get_header(headers, "from") or ""
    recipients = get_header(headers, "to") or ""
    date_header = get_header(headers, "date")
    internal_date = message.get("internalDate")
    received_at = to_valid_iso(date_header) or (
        to_valid_iso(str(int(internal_date) / 1000)) if internal_date else None
    )
    body = extract_gmail_body(payload).strip() or str(message.get("snippet") or "").strip()
    message_id = message.get("id")
    thread_id = message.get("threadId")

    if not message_id or not thread_id or not received_at or not subject:
        return None

    return SourceRecord(
        id=str(message_id),
        user_id=DEV_USER_ID,
        source="gmail",
        thread_id=str(thread_id),
        raw_payload={
            "subject": subject,
            "body": body,
            "from": sender,
            "to": recipients,
            "received_at": received_at,
        },
        received_at=received_at,
    )


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
