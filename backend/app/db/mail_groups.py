from __future__ import annotations

"""Postgres repositories for Gmail messages, mail groups, import state, and deletion guards."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import text

from app.db.repository import get_engine


@dataclass(frozen=True)
class GmailMessageRecord:
    user_id: str
    message_id: str
    gmail_thread_id: str | None
    history_id: str | None
    label_ids: list[str]
    internal_date: str | None
    subject: str | None
    sender: str | None
    recipients: dict[str, Any]
    headers: dict[str, str]
    snippet: str | None
    raw_payload: dict[str, Any]
    html_body_sanitized: str | None
    text_body: str | None
    extracted_signals: dict[str, Any]
    body_hash: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MailGroupRecord:
    id: str
    user_id: str
    group_key: str
    group_type: str
    status: str
    enrichment_status: str
    membership_source: str
    ai_model: str | None
    ai_error: str | None
    ai_generated_at: str | None
    ai_title: str
    ai_summary: str
    labels: list[str]
    action_needed: bool
    action_type: str
    priority: int
    timing_band: str
    dashboard_visible: bool
    latest_message_at: str | None
    latest_message_id: str | None
    generated_from_hash: str
    generated_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MailGroupDetail:
    group: MailGroupRecord
    messages: list[GmailMessageRecord]


@dataclass(frozen=True)
class GmailImportState:
    user_id: str
    last_history_id: str | None
    full_backfill_cursor: str | None
    first_batch_imported_at: str | None
    first_groups_ready_at: str | None
    first_dashboard_ready_at: str | None
    last_import_started_at: str | None
    last_import_completed_at: str | None
    last_sync_error: str | None
    updated_at: str


def user_can_write_gmail(database_url: str, *, user_id: str) -> bool:
    with get_engine(database_url).connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT users.id
                FROM users
                JOIN google_oauth_tokens tokens ON tokens.user_id = users.id
                WHERE users.id = :user_id
                  AND users.access_enabled = TRUE
                  AND users.google_disconnected_at IS NULL
                  AND users.google_data_delete_requested_at IS NULL
                  AND users.google_data_deleted_at IS NULL
                """
            ),
            {"user_id": user_id},
        ).first()
    return row is not None


def clear_google_guard_state(database_url: str, *, user_id: str) -> None:
    with get_engine(database_url).begin() as connection:
        connection.execute(
            text(
                """
                UPDATE users
                SET google_disconnected_at = NULL,
                    google_data_delete_requested_at = NULL,
                    google_data_deleted_at = NULL,
                    updated_at = now()
                WHERE id = :user_id
                """
            ),
            {"user_id": user_id},
        )


def mark_google_disconnected(database_url: str, *, user_id: str) -> None:
    with get_engine(database_url).begin() as connection:
        connection.execute(
            text("UPDATE users SET google_disconnected_at = now(), updated_at = now() WHERE id = :user_id"),
            {"user_id": user_id},
        )


def delete_user_mail_data(database_url: str, *, user_id: str) -> None:
    with get_engine(database_url).begin() as connection:
        connection.execute(
            text(
                """
                UPDATE users
                SET google_data_delete_requested_at = COALESCE(google_data_delete_requested_at, now()),
                    updated_at = now()
                WHERE id = :user_id
                """
            ),
            {"user_id": user_id},
        )
        for table in ["mail_group_members", "mail_groups", "gmail_messages", "gmail_import_state"]:
            connection.execute(text(f"DELETE FROM {table} WHERE user_id = :user_id"), {"user_id": user_id})
        connection.execute(
            text(
                """
                UPDATE users
                SET google_data_deleted_at = now(), updated_at = now()
                WHERE id = :user_id
                """
            ),
            {"user_id": user_id},
        )


def mark_import_started(database_url: str, *, user_id: str) -> None:
    with get_engine(database_url).begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO gmail_import_state (user_id, last_import_started_at, updated_at)
                VALUES (:user_id, now(), now())
                ON CONFLICT (user_id) DO UPDATE SET
                  last_import_started_at = now(),
                  last_sync_error = NULL,
                  updated_at = now()
                """
            ),
            {"user_id": user_id},
        )


def mark_import_completed(
    database_url: str,
    *,
    user_id: str,
    first_batch: bool = False,
    groups_ready: bool = False,
    dashboard_ready: bool = False,
    last_history_id: str | None = None,
    full_backfill_cursor: str | None = None,
) -> None:
    with get_engine(database_url).begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO gmail_import_state (
                  user_id, last_history_id, full_backfill_cursor, first_batch_imported_at,
                  first_groups_ready_at, first_dashboard_ready_at, last_import_completed_at, updated_at
                ) VALUES (
                  :user_id, :last_history_id, :full_backfill_cursor,
                  CASE WHEN :first_batch THEN now() ELSE NULL END,
                  CASE WHEN :groups_ready THEN now() ELSE NULL END,
                  CASE WHEN :dashboard_ready THEN now() ELSE NULL END,
                  now(), now()
                )
                ON CONFLICT (user_id) DO UPDATE SET
                  last_history_id = COALESCE(excluded.last_history_id, gmail_import_state.last_history_id),
                  full_backfill_cursor = COALESCE(excluded.full_backfill_cursor, gmail_import_state.full_backfill_cursor),
                  first_batch_imported_at = COALESCE(gmail_import_state.first_batch_imported_at, excluded.first_batch_imported_at),
                  first_groups_ready_at = COALESCE(gmail_import_state.first_groups_ready_at, excluded.first_groups_ready_at),
                  first_dashboard_ready_at = COALESCE(gmail_import_state.first_dashboard_ready_at, excluded.first_dashboard_ready_at),
                  last_import_completed_at = now(),
                  last_sync_error = NULL,
                  updated_at = now()
                """
            ),
            {
                "user_id": user_id,
                "last_history_id": last_history_id,
                "full_backfill_cursor": full_backfill_cursor,
                "first_batch": first_batch,
                "groups_ready": groups_ready,
                "dashboard_ready": dashboard_ready,
            },
        )


def mark_import_error(database_url: str, *, user_id: str, error: str) -> None:
    with get_engine(database_url).begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO gmail_import_state (user_id, last_sync_error, updated_at)
                VALUES (:user_id, :error, now())
                ON CONFLICT (user_id) DO UPDATE SET last_sync_error = excluded.last_sync_error, updated_at = now()
                """
            ),
            {"user_id": user_id, "error": error[:4000]},
        )


def get_import_state(database_url: str, *, user_id: str) -> GmailImportState | None:
    with get_engine(database_url).connect() as connection:
        row = connection.execute(text("SELECT * FROM gmail_import_state WHERE user_id = :user_id"), {"user_id": user_id}).mappings().first()
    return _state_from_row(row) if row is not None else None


def upsert_gmail_messages(database_url: str, messages: Iterable[GmailMessageRecord]) -> int:
    rows = list(messages)
    if not rows:
        return 0
    with get_engine(database_url).begin() as connection:
        for message in rows:
            connection.execute(
                text(
                    """
                    INSERT INTO gmail_messages (
                      user_id, message_id, gmail_thread_id, history_id, label_ids_json, internal_date,
                      subject, sender, recipients_json, headers_json, snippet, raw_payload_json,
                      html_body_sanitized, text_body, extracted_signals_json, body_hash, created_at, updated_at
                    ) VALUES (
                      :user_id, :message_id, :gmail_thread_id, :history_id, :label_ids_json, :internal_date,
                      :subject, :sender, :recipients_json, :headers_json, :snippet, :raw_payload_json,
                      :html_body_sanitized, :text_body, :extracted_signals_json, :body_hash, now(), now()
                    )
                    ON CONFLICT (user_id, message_id) DO UPDATE SET
                      gmail_thread_id = excluded.gmail_thread_id,
                      history_id = excluded.history_id,
                      label_ids_json = excluded.label_ids_json,
                      internal_date = excluded.internal_date,
                      subject = excluded.subject,
                      sender = excluded.sender,
                      recipients_json = excluded.recipients_json,
                      headers_json = excluded.headers_json,
                      snippet = excluded.snippet,
                      raw_payload_json = excluded.raw_payload_json,
                      html_body_sanitized = excluded.html_body_sanitized,
                      text_body = excluded.text_body,
                      extracted_signals_json = excluded.extracted_signals_json,
                      body_hash = excluded.body_hash,
                      updated_at = now()
                    """
                ),
                _message_params(message),
            )
    return len(rows)


def list_recent_messages(database_url: str, *, user_id: str, limit: int = 500) -> list[GmailMessageRecord]:
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT * FROM gmail_messages
                WHERE user_id = :user_id
                ORDER BY internal_date DESC NULLS LAST, updated_at DESC
                LIMIT :limit
                """
            ),
            {"user_id": user_id, "limit": limit},
        ).mappings().all()
    return [_message_from_row(row) for row in rows]


def get_mail_group_by_key(database_url: str, *, user_id: str, group_key: str) -> MailGroupRecord | None:
    with get_engine(database_url).connect() as connection:
        row = connection.execute(
            text("SELECT * FROM mail_groups WHERE user_id = :user_id AND group_key = :group_key"),
            {"user_id": user_id, "group_key": group_key},
        ).mappings().first()
    return _group_from_row(row) if row is not None else None


def list_group_messages(database_url: str, *, user_id: str, group_id: str) -> list[GmailMessageRecord]:
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT messages.*
                FROM mail_group_members members
                JOIN gmail_messages messages
                  ON messages.user_id = members.user_id AND messages.message_id = members.gmail_message_id
                WHERE members.user_id = :user_id AND members.group_id = :group_id
                ORDER BY messages.internal_date ASC NULLS LAST, messages.created_at ASC
                """
            ),
            {"user_id": user_id, "group_id": group_id},
        ).mappings().all()
    return [_message_from_row(row) for row in rows]


def upsert_mail_group(
    database_url: str,
    *,
    user_id: str,
    group_key: str,
    group_type: str,
    ai_title: str,
    ai_summary: str,
    labels: list[str],
    action_needed: bool,
    action_type: str,
    priority: int,
    timing_band: str,
    dashboard_visible: bool,
    latest_message_at: str | None,
    latest_message_id: str | None,
    generated_from_hash: str,
    generated_at: str | None,
    enrichment_status: str = "pending",
    membership_source: str = "deterministic_candidate",
    ai_model: str | None = None,
    ai_error: str | None = None,
    ai_generated_at: str | None = None,
) -> MailGroupRecord:
    with get_engine(database_url).begin() as connection:
        row = connection.execute(
            text(
                """
                INSERT INTO mail_groups (
                  id, user_id, group_key, group_type, status, enrichment_status, membership_source,
                  ai_model, ai_error, ai_generated_at, ai_title, ai_summary, labels_json,
                  action_needed, action_type, priority, timing_band, dashboard_visible, latest_message_at,
                  latest_message_id, generated_from_hash, generated_at, created_at, updated_at
                ) VALUES (
                  :id, :user_id, :group_key, :group_type, 'active', :enrichment_status, :membership_source,
                  :ai_model, :ai_error, :ai_generated_at, :ai_title, :ai_summary, :labels_json,
                  :action_needed, :action_type, :priority, :timing_band, :dashboard_visible, :latest_message_at,
                  :latest_message_id, :generated_from_hash, :generated_at, now(), now()
                )
                ON CONFLICT (user_id, group_key) DO UPDATE SET
                  group_type = excluded.group_type,
                  enrichment_status = excluded.enrichment_status,
                  membership_source = excluded.membership_source,
                  ai_model = excluded.ai_model,
                  ai_error = excluded.ai_error,
                  ai_generated_at = excluded.ai_generated_at,
                  ai_title = excluded.ai_title,
                  ai_summary = excluded.ai_summary,
                  labels_json = excluded.labels_json,
                  action_needed = excluded.action_needed,
                  action_type = excluded.action_type,
                  priority = excluded.priority,
                  timing_band = excluded.timing_band,
                  dashboard_visible = excluded.dashboard_visible,
                  latest_message_at = excluded.latest_message_at,
                  latest_message_id = excluded.latest_message_id,
                  generated_from_hash = excluded.generated_from_hash,
                  generated_at = excluded.generated_at,
                  updated_at = now()
                RETURNING *
                """
            ),
            {
                "id": str(uuid4()),
                "user_id": user_id,
                "group_key": group_key,
                "group_type": group_type,
                "enrichment_status": enrichment_status,
                "membership_source": membership_source,
                "ai_model": ai_model,
                "ai_error": ai_error,
                "ai_generated_at": ai_generated_at,
                "ai_title": ai_title,
                "ai_summary": ai_summary,
                "labels_json": json.dumps(labels, ensure_ascii=True),
                "action_needed": action_needed,
                "action_type": action_type,
                "priority": priority,
                "timing_band": timing_band,
                "dashboard_visible": dashboard_visible,
                "latest_message_at": latest_message_at,
                "latest_message_id": latest_message_id,
                "generated_from_hash": generated_from_hash,
                "generated_at": generated_at,
            },
        ).mappings().one()
    return _group_from_row(row)


def replace_group_members(
    database_url: str,
    *,
    user_id: str,
    group_id: str,
    members: list[tuple[GmailMessageRecord, str, float]],
) -> None:
    with get_engine(database_url).begin() as connection:
        connection.execute(text("DELETE FROM mail_group_members WHERE user_id = :user_id AND group_id = :group_id"), {"user_id": user_id, "group_id": group_id})
        for message, reason, confidence in members:
            connection.execute(
                text(
                    """
                    INSERT INTO mail_group_members (id, user_id, group_id, gmail_message_id, gmail_thread_id, reason, confidence, created_at)
                    VALUES (:id, :user_id, :group_id, :gmail_message_id, :gmail_thread_id, :reason, :confidence, now())
                    ON CONFLICT (user_id, group_id, gmail_message_id) DO NOTHING
                    """
                ),
                {
                    "id": str(uuid4()),
                    "user_id": user_id,
                    "group_id": group_id,
                    "gmail_message_id": message.message_id,
                    "gmail_thread_id": message.gmail_thread_id,
                    "reason": reason,
                    "confidence": confidence,
                },
            )


def list_mail_groups(database_url: str, *, user_id: str, limit: int = 150) -> list[MailGroupRecord]:
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT * FROM mail_groups
                WHERE user_id = :user_id
                  AND status = 'active'
                  AND enrichment_status = 'ready'
                ORDER BY latest_message_at DESC NULLS LAST, updated_at DESC
                LIMIT :limit
                """
            ),
            {"user_id": user_id, "limit": limit},
        ).mappings().all()
    return [_group_from_row(row) for row in rows]


def list_dashboard_mail_groups(database_url: str, *, user_id: str, since_iso: str, limit: int = 80) -> list[MailGroupRecord]:
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT * FROM mail_groups
                WHERE user_id = :user_id
                  AND status = 'active'
                  AND enrichment_status = 'ready'
                  AND dashboard_visible = TRUE
                  AND latest_message_at >= :since_iso
                ORDER BY priority DESC, latest_message_at DESC NULLS LAST
                LIMIT :limit
                """
            ),
            {"user_id": user_id, "since_iso": since_iso, "limit": limit},
        ).mappings().all()
    return [_group_from_row(row) for row in rows]


def get_mail_group_detail(database_url: str, *, user_id: str, group_id: str) -> MailGroupDetail | None:
    with get_engine(database_url).connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT *
                FROM mail_groups
                WHERE user_id = :user_id
                  AND id = :id
                  AND status = 'active'
                  AND enrichment_status = 'ready'
                """
            ),
            {"user_id": user_id, "id": group_id},
        ).mappings().first()
    if row is None:
        return None
    group = _group_from_row(row)
    return MailGroupDetail(group=group, messages=list_group_messages(database_url, user_id=user_id, group_id=group.id))


def count_mail_groups(database_url: str, *, user_id: str) -> int:
    with get_engine(database_url).connect() as connection:
        value = connection.execute(
            text(
                """
                SELECT COUNT(*)
                FROM mail_groups
                WHERE user_id = :user_id
                  AND status = 'active'
                  AND enrichment_status = 'ready'
                """
            ),
            {"user_id": user_id},
        ).scalar_one()
    return int(value)


def count_mail_groups_by_enrichment_status(database_url: str, *, user_id: str) -> dict[str, int]:
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT enrichment_status, COUNT(*) AS count
                FROM mail_groups
                WHERE user_id = :user_id
                  AND status = 'active'
                GROUP BY enrichment_status
                """
            ),
            {"user_id": user_id},
        ).mappings().all()
    return {str(row["enrichment_status"]): int(row["count"]) for row in rows}


def _message_params(message: GmailMessageRecord) -> dict[str, Any]:
    return {
        "user_id": message.user_id,
        "message_id": message.message_id,
        "gmail_thread_id": message.gmail_thread_id,
        "history_id": message.history_id,
        "label_ids_json": json.dumps(message.label_ids, ensure_ascii=True),
        "internal_date": message.internal_date,
        "subject": message.subject,
        "sender": message.sender,
        "recipients_json": json.dumps(message.recipients, ensure_ascii=True),
        "headers_json": json.dumps(message.headers, ensure_ascii=True),
        "snippet": message.snippet,
        "raw_payload_json": json.dumps(message.raw_payload, ensure_ascii=True),
        "html_body_sanitized": message.html_body_sanitized,
        "text_body": message.text_body,
        "extracted_signals_json": json.dumps(message.extracted_signals, ensure_ascii=True),
        "body_hash": message.body_hash,
    }


def _message_from_row(row) -> GmailMessageRecord:
    return GmailMessageRecord(
        user_id=str(row["user_id"]),
        message_id=str(row["message_id"]),
        gmail_thread_id=str(row["gmail_thread_id"]) if row["gmail_thread_id"] is not None else None,
        history_id=str(row["history_id"]) if row["history_id"] is not None else None,
        label_ids=json.loads(row["label_ids_json"] or "[]"),
        internal_date=_iso(row["internal_date"]) if row["internal_date"] is not None else None,
        subject=str(row["subject"]) if row["subject"] is not None else None,
        sender=str(row["sender"]) if row["sender"] is not None else None,
        recipients=json.loads(row["recipients_json"] or "{}"),
        headers=json.loads(row["headers_json"] or "{}"),
        snippet=str(row["snippet"]) if row["snippet"] is not None else None,
        raw_payload=json.loads(row["raw_payload_json"] or "{}"),
        html_body_sanitized=str(row["html_body_sanitized"]) if row["html_body_sanitized"] is not None else None,
        text_body=str(row["text_body"]) if row["text_body"] is not None else None,
        extracted_signals=json.loads(row["extracted_signals_json"] or "{}"),
        body_hash=str(row["body_hash"]),
        created_at=_iso(row["created_at"]),
        updated_at=_iso(row["updated_at"]),
    )


def _group_from_row(row) -> MailGroupRecord:
    return MailGroupRecord(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        group_key=str(row["group_key"]),
        group_type=str(row["group_type"]),
        status=str(row["status"]),
        enrichment_status=str(row["enrichment_status"]),
        membership_source=str(row["membership_source"]),
        ai_model=str(row["ai_model"]) if row["ai_model"] is not None else None,
        ai_error=str(row["ai_error"]) if row["ai_error"] is not None else None,
        ai_generated_at=_iso(row["ai_generated_at"]) if row["ai_generated_at"] is not None else None,
        ai_title=str(row["ai_title"]),
        ai_summary=str(row["ai_summary"]),
        labels=json.loads(row["labels_json"] or "[]"),
        action_needed=bool(row["action_needed"]),
        action_type=str(row["action_type"]),
        priority=int(row["priority"]),
        timing_band=str(row["timing_band"]),
        dashboard_visible=bool(row["dashboard_visible"]),
        latest_message_at=_iso(row["latest_message_at"]) if row["latest_message_at"] is not None else None,
        latest_message_id=str(row["latest_message_id"]) if row["latest_message_id"] is not None else None,
        generated_from_hash=str(row["generated_from_hash"]),
        generated_at=_iso(row["generated_at"]) if row["generated_at"] is not None else None,
        created_at=_iso(row["created_at"]),
        updated_at=_iso(row["updated_at"]),
    )


def _state_from_row(row) -> GmailImportState:
    return GmailImportState(
        user_id=str(row["user_id"]),
        last_history_id=str(row["last_history_id"]) if row["last_history_id"] is not None else None,
        full_backfill_cursor=str(row["full_backfill_cursor"]) if row["full_backfill_cursor"] is not None else None,
        first_batch_imported_at=_iso(row["first_batch_imported_at"]) if row["first_batch_imported_at"] is not None else None,
        first_groups_ready_at=_iso(row["first_groups_ready_at"]) if row["first_groups_ready_at"] is not None else None,
        first_dashboard_ready_at=_iso(row["first_dashboard_ready_at"]) if row["first_dashboard_ready_at"] is not None else None,
        last_import_started_at=_iso(row["last_import_started_at"]) if row["last_import_started_at"] is not None else None,
        last_import_completed_at=_iso(row["last_import_completed_at"]) if row["last_import_completed_at"] is not None else None,
        last_sync_error=str(row["last_sync_error"]) if row["last_sync_error"] is not None else None,
        updated_at=_iso(row["updated_at"]),
    )


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)
