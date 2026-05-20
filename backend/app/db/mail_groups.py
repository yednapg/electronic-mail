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
    html_render_document: str | None
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
class ManualTaskRecord:
    id: str
    user_id: str
    entity_id: str
    title: str
    notes: str | None
    section: str
    due_at: str | None
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class EntityOutcomeRecord:
    id: str
    user_id: str
    entity_id: str
    outcome_type: str
    snooze_until: str | None
    note: str | None
    created_at: str


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
    gmail_watch_history_id: str | None
    gmail_watch_expiration_at: str | None
    gmail_watch_started_at: str | None
    gmail_watch_error: str | None
    updated_at: str


@dataclass(frozen=True)
class AppSessionSnapshotRecord:
    user_id: str
    dashboard: dict[str, Any]
    mailbox: dict[str, Any]
    sync: dict[str, Any]
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
        connection.execute(
            text(
                """
                UPDATE gmail_import_state
                SET gmail_watch_history_id = NULL,
                    gmail_watch_expiration_at = NULL,
                    gmail_watch_started_at = NULL,
                    gmail_watch_error = NULL,
                    updated_at = now()
                WHERE user_id = :user_id
                """
            ),
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
        for table in ["app_session_snapshots", "mail_group_members", "mail_groups", "gmail_messages", "gmail_import_state"]:
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
    clear_full_backfill_cursor: bool = False,
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
                  full_backfill_cursor = CASE
                    WHEN :clear_full_backfill_cursor THEN NULL
                    ELSE COALESCE(excluded.full_backfill_cursor, gmail_import_state.full_backfill_cursor)
                  END,
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
                "clear_full_backfill_cursor": clear_full_backfill_cursor,
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


def mark_gmail_watch_started(
    database_url: str,
    *,
    user_id: str,
    history_id: str | None,
    expiration_at: str | None,
) -> None:
    with get_engine(database_url).begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO gmail_import_state (
                  user_id, gmail_watch_history_id, gmail_watch_expiration_at,
                  gmail_watch_started_at, gmail_watch_error, updated_at
                )
                VALUES (:user_id, :history_id, :expiration_at, now(), NULL, now())
                ON CONFLICT (user_id) DO UPDATE SET
                  gmail_watch_history_id = excluded.gmail_watch_history_id,
                  gmail_watch_expiration_at = excluded.gmail_watch_expiration_at,
                  gmail_watch_started_at = now(),
                  gmail_watch_error = NULL,
                  updated_at = now()
                """
            ),
            {"user_id": user_id, "history_id": history_id, "expiration_at": expiration_at},
        )


def mark_gmail_watch_error(database_url: str, *, user_id: str, error: str) -> None:
    with get_engine(database_url).begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO gmail_import_state (user_id, gmail_watch_error, updated_at)
                VALUES (:user_id, :error, now())
                ON CONFLICT (user_id) DO UPDATE SET
                  gmail_watch_error = excluded.gmail_watch_error,
                  updated_at = now()
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
                      html_body_sanitized, html_render_document, text_body, extracted_signals_json, body_hash, created_at, updated_at
                    ) VALUES (
                      :user_id, :message_id, :gmail_thread_id, :history_id, :label_ids_json, :internal_date,
                      :subject, :sender, :recipients_json, :headers_json, :snippet, :raw_payload_json,
                      :html_body_sanitized, :html_render_document, :text_body, :extracted_signals_json, :body_hash, now(), now()
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
                      raw_payload_json = CASE
                        WHEN excluded.html_render_document IS NOT NULL OR excluded.html_body_sanitized IS NOT NULL OR excluded.text_body IS NOT NULL
                        THEN excluded.raw_payload_json
                        ELSE gmail_messages.raw_payload_json
                      END,
                      html_body_sanitized = COALESCE(excluded.html_body_sanitized, gmail_messages.html_body_sanitized),
                      html_render_document = COALESCE(excluded.html_render_document, gmail_messages.html_render_document),
                      text_body = COALESCE(excluded.text_body, gmail_messages.text_body),
                      extracted_signals_json = excluded.extracted_signals_json,
                      body_hash = excluded.body_hash,
                      updated_at = now()
                    """
                ),
                _message_params(message),
            )
    return len(rows)


def delete_gmail_messages(database_url: str, *, user_id: str, message_ids: list[str]) -> list[str]:
    unique_message_ids = list(dict.fromkeys([message_id for message_id in message_ids if message_id]))
    if not unique_message_ids:
        return []
    with get_engine(database_url).begin() as connection:
        rows = connection.execute(
            text(
                """
                SELECT DISTINCT group_id
                FROM mail_group_members
                WHERE user_id = :user_id
                  AND gmail_message_id = ANY(:message_ids)
                """
            ),
            {"user_id": user_id, "message_ids": unique_message_ids},
        ).mappings().all()
        connection.execute(
            text(
                """
                DELETE FROM mail_group_members
                WHERE user_id = :user_id
                  AND gmail_message_id = ANY(:message_ids)
                """
            ),
            {"user_id": user_id, "message_ids": unique_message_ids},
        )
        connection.execute(
            text(
                """
                DELETE FROM gmail_messages
                WHERE user_id = :user_id
                  AND message_id = ANY(:message_ids)
                """
            ),
            {"user_id": user_id, "message_ids": unique_message_ids},
        )
    return [str(row["group_id"]) for row in rows]


def prune_empty_mail_groups(database_url: str, *, user_id: str, group_ids: list[str]) -> list[str]:
    unique_group_ids = list(dict.fromkeys([group_id for group_id in group_ids if group_id]))
    if not unique_group_ids:
        return []
    with get_engine(database_url).begin() as connection:
        rows = connection.execute(
            text(
                """
                UPDATE mail_groups AS groups
                SET
                  status = 'deleted',
                  enrichment_status = 'ready',
                  dashboard_visible = FALSE,
                  updated_at = now()
                WHERE groups.user_id = :user_id
                  AND groups.id = ANY(:group_ids)
                  AND NOT EXISTS (
                    SELECT 1
                    FROM mail_group_members members
                    WHERE members.user_id = groups.user_id
                      AND members.group_id = groups.id
                  )
                RETURNING groups.id
                """
            ),
            {"user_id": user_id, "group_ids": unique_group_ids},
        ).mappings().all()
    return [str(row["id"]) for row in rows]


def mark_mail_groups_pending(database_url: str, *, user_id: str, group_ids: list[str]) -> list[str]:
    unique_group_ids = list(dict.fromkeys([group_id for group_id in group_ids if group_id]))
    if not unique_group_ids:
        return []
    with get_engine(database_url).begin() as connection:
        rows = connection.execute(
            text(
                """
                UPDATE mail_groups AS groups
                SET
                  enrichment_status = 'pending',
                  generated_at = NULL,
                  ai_model = NULL,
                  ai_error = NULL,
                  ai_generated_at = NULL,
                  dashboard_visible = FALSE,
                  updated_at = now()
                WHERE groups.user_id = :user_id
                  AND groups.id = ANY(:group_ids)
                  AND groups.status = 'active'
                RETURNING groups.id
                """
            ),
            {"user_id": user_id, "group_ids": unique_group_ids},
        ).mappings().all()
    return [str(row["id"]) for row in rows]


def list_messages_by_ids(database_url: str, *, user_id: str, message_ids: list[str]) -> list[GmailMessageRecord]:
    if not message_ids:
        return []
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT *
                FROM gmail_messages
                WHERE user_id = :user_id
                  AND message_id = ANY(:message_ids)
                ORDER BY internal_date DESC NULLS LAST, updated_at DESC
                """
            ),
            {"user_id": user_id, "message_ids": message_ids},
        ).mappings().all()
    by_id = {str(row["message_id"]): _message_from_row(row) for row in rows}
    return [by_id[message_id] for message_id in message_ids if message_id in by_id]


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


def list_recent_messages_since(database_url: str, *, user_id: str, since_iso: str, limit: int = 500) -> list[GmailMessageRecord]:
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT * FROM gmail_messages
                WHERE user_id = :user_id
                  AND internal_date >= :since_iso
                ORDER BY internal_date DESC NULLS LAST, updated_at DESC
                LIMIT :limit
                """
            ),
            {"user_id": user_id, "since_iso": since_iso, "limit": limit},
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


def list_messages_for_groups(database_url: str, *, user_id: str, group_ids: list[str]) -> dict[str, list[GmailMessageRecord]]:
    if not group_ids:
        return {}
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT
                  members.group_id AS member_group_id,
                  messages.user_id,
                  messages.message_id,
                  messages.gmail_thread_id,
                  messages.history_id,
                  messages.label_ids_json,
                  messages.internal_date,
                  messages.subject,
                  messages.sender,
                  messages.recipients_json,
                  messages.headers_json,
                  messages.snippet,
                  '{}' AS raw_payload_json,
                  NULL AS html_body_sanitized,
                  NULL AS html_render_document,
                  NULL AS text_body,
                  messages.extracted_signals_json,
                  messages.body_hash,
                  messages.created_at,
                  messages.updated_at
                FROM mail_group_members members
                JOIN gmail_messages messages
                  ON messages.user_id = members.user_id AND messages.message_id = members.gmail_message_id
                WHERE members.user_id = :user_id AND members.group_id = ANY(:group_ids)
                ORDER BY members.group_id, messages.internal_date ASC NULLS LAST, messages.created_at ASC
                """
            ),
            {"user_id": user_id, "group_ids": group_ids},
        ).mappings().all()
    grouped: dict[str, list[GmailMessageRecord]] = {group_id: [] for group_id in group_ids}
    for row in rows:
        grouped.setdefault(str(row["member_group_id"]), []).append(_message_from_row(row))
    return grouped


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


def append_group_members(
    database_url: str,
    *,
    user_id: str,
    group_id: str,
    members: list[tuple[GmailMessageRecord, str, float]],
) -> None:
    if not members:
        return
    with get_engine(database_url).begin() as connection:
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


def create_manual_task(
    database_url: str,
    *,
    user_id: str,
    title: str,
    notes: str | None,
    section: str = "today",
    due_at: str | None = None,
) -> ManualTaskRecord:
    task_id = str(uuid4())
    entity_id = f"manual-task:{task_id}"
    with get_engine(database_url).begin() as connection:
        row = connection.execute(
            text(
                """
                INSERT INTO manual_tasks (
                  id, user_id, entity_id, title, notes, section, due_at, status, created_at, updated_at
                ) VALUES (
                  :id, :user_id, :entity_id, :title, :notes, :section, :due_at, 'open', now(), now()
                )
                RETURNING *
                """
            ),
            {
                "id": task_id,
                "user_id": user_id,
                "entity_id": entity_id,
                "title": title,
                "notes": notes,
                "section": section,
                "due_at": due_at,
            },
        ).mappings().one()
    return _manual_task_from_row(row)


def update_manual_task(
    database_url: str,
    task_id: str,
    *,
    user_id: str,
    title: str | None = None,
    notes: str | None = None,
    section: str | None = None,
    due_at: str | None = None,
    status: str | None = None,
) -> ManualTaskRecord | None:
    existing = get_manual_task(database_url, task_id, user_id=user_id)
    if existing is None:
        return None
    with get_engine(database_url).begin() as connection:
        row = connection.execute(
            text(
                """
                UPDATE manual_tasks
                SET title = :title,
                    notes = :notes,
                    section = :section,
                    due_at = :due_at,
                    status = :status,
                    updated_at = now()
                WHERE id = :id AND user_id = :user_id
                RETURNING *
                """
            ),
            {
                "id": task_id,
                "user_id": user_id,
                "title": title if title is not None else existing.title,
                "notes": notes if notes is not None else existing.notes,
                "section": section if section is not None else existing.section,
                "due_at": due_at if due_at is not None else existing.due_at,
                "status": status if status is not None else existing.status,
            },
        ).mappings().first()
    return _manual_task_from_row(row) if row is not None else None


def get_manual_task(database_url: str, task_id: str, *, user_id: str) -> ManualTaskRecord | None:
    with get_engine(database_url).connect() as connection:
        row = connection.execute(
            text("SELECT * FROM manual_tasks WHERE id = :id AND user_id = :user_id"),
            {"id": task_id, "user_id": user_id},
        ).mappings().first()
    return _manual_task_from_row(row) if row is not None else None


def get_manual_task_by_entity_id(database_url: str, *, user_id: str, entity_id: str) -> ManualTaskRecord | None:
    with get_engine(database_url).connect() as connection:
        row = connection.execute(
            text("SELECT * FROM manual_tasks WHERE entity_id = :entity_id AND user_id = :user_id"),
            {"entity_id": entity_id, "user_id": user_id},
        ).mappings().first()
    return _manual_task_from_row(row) if row is not None else None


def list_open_manual_tasks(database_url: str, *, user_id: str) -> list[ManualTaskRecord]:
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT *
                FROM manual_tasks
                WHERE user_id = :user_id
                  AND status = 'open'
                ORDER BY
                  CASE section WHEN 'now' THEN 0 WHEN 'today' THEN 1 ELSE 2 END,
                  COALESCE(due_at, created_at) ASC,
                  created_at ASC
                """
            ),
            {"user_id": user_id},
        ).mappings().all()
    return [_manual_task_from_row(row) for row in rows]


def append_entity_outcome(
    database_url: str,
    *,
    user_id: str,
    entity_id: str,
    outcome_type: str,
    snooze_until: str | None = None,
    note: str | None = None,
) -> EntityOutcomeRecord:
    with get_engine(database_url).begin() as connection:
        row = connection.execute(
            text(
                """
                INSERT INTO entity_outcomes (id, user_id, entity_id, outcome_type, snooze_until, note, created_at)
                VALUES (:id, :user_id, :entity_id, :outcome_type, :snooze_until, :note, now())
                RETURNING *
                """
            ),
            {
                "id": str(uuid4()),
                "user_id": user_id,
                "entity_id": entity_id,
                "outcome_type": outcome_type,
                "snooze_until": snooze_until,
                "note": note,
            },
        ).mappings().one()
    return _entity_outcome_from_row(row)


def get_latest_entity_outcomes(
    database_url: str,
    *,
    user_id: str,
    entity_ids: list[str] | None = None,
) -> dict[str, EntityOutcomeRecord]:
    if entity_ids is not None:
        entity_ids = [entity_id for entity_id in dict.fromkeys(entity_ids) if entity_id]
        if not entity_ids:
            return {}
    with get_engine(database_url).connect() as connection:
        if entity_ids is None:
            rows = connection.execute(
                text(
                    """
                    SELECT DISTINCT ON (entity_id) *
                    FROM entity_outcomes
                    WHERE user_id = :user_id
                    ORDER BY entity_id, created_at DESC, id DESC
                    """
                ),
                {"user_id": user_id},
            ).mappings().all()
        else:
            rows = connection.execute(
                text(
                    """
                    SELECT DISTINCT ON (entity_id) *
                    FROM entity_outcomes
                    WHERE user_id = :user_id
                      AND entity_id = ANY(:entity_ids)
                    ORDER BY entity_id, created_at DESC, id DESC
                    """
                ),
                {"user_id": user_id, "entity_ids": entity_ids},
            ).mappings().all()
    return {str(row["entity_id"]): _entity_outcome_from_row(row) for row in rows}


def list_mail_groups(database_url: str, *, user_id: str, limit: int = 150, include_pending: bool = False) -> list[MailGroupRecord]:
    if include_pending:
        with get_engine(database_url).connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT * FROM mail_groups
                    WHERE user_id = :user_id
                      AND status = 'active'
                    ORDER BY latest_message_at DESC NULLS LAST, updated_at DESC
                    LIMIT :limit
                    """
                ),
                {"user_id": user_id, "limit": limit},
            ).mappings().all()
        return [_group_from_row(row) for row in rows]

    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT * FROM mail_groups
                WHERE user_id = :user_id
                  AND status = 'active'
                  AND (:include_pending OR enrichment_status = 'ready')
                ORDER BY
                  CASE WHEN enrichment_status = 'ready' THEN 0 ELSE 1 END,
                  latest_message_at DESC NULLS LAST,
                  updated_at DESC
                LIMIT :limit
                """
            ),
            {"user_id": user_id, "limit": limit, "include_pending": include_pending},
        ).mappings().all()
    return [_group_from_row(row) for row in rows]


def list_pending_mail_groups(database_url: str, *, user_id: str, limit: int = 10) -> list[MailGroupRecord]:
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT *
                FROM mail_groups
                WHERE user_id = :user_id
                  AND status = 'active'
                  AND enrichment_status = 'pending'
                ORDER BY latest_message_at DESC NULLS LAST, priority DESC, updated_at ASC
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


def count_ready_mail_groups_since(database_url: str, *, user_id: str, since_iso: str) -> int:
    with get_engine(database_url).connect() as connection:
        value = connection.execute(
            text(
                """
                SELECT COUNT(*)
                FROM mail_groups
                WHERE user_id = :user_id
                  AND status = 'active'
                  AND enrichment_status = 'ready'
                  AND latest_message_at >= :since_iso
                """
            ),
            {"user_id": user_id, "since_iso": since_iso},
        ).scalar_one()
    return int(value)


def count_dashboard_mail_groups(database_url: str, *, user_id: str, since_iso: str) -> int:
    with get_engine(database_url).connect() as connection:
        value = connection.execute(
            text(
                """
                SELECT COUNT(*)
                FROM mail_groups
                WHERE user_id = :user_id
                  AND status = 'active'
                  AND enrichment_status = 'ready'
                  AND dashboard_visible = TRUE
                  AND latest_message_at >= :since_iso
                """
            ),
            {"user_id": user_id, "since_iso": since_iso},
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


def oldest_imported_message_at(database_url: str, *, user_id: str) -> str | None:
    with get_engine(database_url).connect() as connection:
        value = connection.execute(
            text(
                """
                SELECT MIN(internal_date)
                FROM gmail_messages
                WHERE user_id = :user_id
                """
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
    return _iso(value) if value is not None else None


def get_app_session_snapshot(database_url: str, *, user_id: str) -> AppSessionSnapshotRecord | None:
    with get_engine(database_url).connect() as connection:
        row = connection.execute(
            text("SELECT * FROM app_session_snapshots WHERE user_id = :user_id"),
            {"user_id": user_id},
        ).mappings().first()
    if row is None:
        return None
    return AppSessionSnapshotRecord(
        user_id=str(row["user_id"]),
        dashboard=json.loads(row["dashboard_json"] or "{}"),
        mailbox=json.loads(row["mailbox_json"] or "{}"),
        sync=json.loads(row["sync_json"] or "{}"),
        updated_at=_iso(row["updated_at"]),
    )


def upsert_app_session_snapshot(
    database_url: str,
    *,
    user_id: str,
    dashboard: dict[str, Any],
    mailbox: dict[str, Any],
    sync: dict[str, Any],
) -> None:
    with get_engine(database_url).begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO app_session_snapshots (user_id, dashboard_json, mailbox_json, sync_json, updated_at)
                VALUES (:user_id, :dashboard_json, :mailbox_json, :sync_json, now())
                ON CONFLICT (user_id) DO UPDATE SET
                  dashboard_json = excluded.dashboard_json,
                  mailbox_json = excluded.mailbox_json,
                  sync_json = excluded.sync_json,
                  updated_at = now()
                """
            ),
            {
                "user_id": user_id,
                "dashboard_json": json.dumps(dashboard, ensure_ascii=True),
                "mailbox_json": json.dumps(mailbox, ensure_ascii=True),
                "sync_json": json.dumps(sync, ensure_ascii=True),
            },
        )


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
        "html_render_document": message.html_render_document,
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
        html_render_document=str(row["html_render_document"]) if row["html_render_document"] is not None else None,
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


def _manual_task_from_row(row) -> ManualTaskRecord:
    return ManualTaskRecord(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        entity_id=str(row["entity_id"]),
        title=str(row["title"]),
        notes=str(row["notes"]) if row["notes"] is not None else None,
        section=str(row["section"]),
        due_at=_iso(row["due_at"]) if row["due_at"] is not None else None,
        status=str(row["status"]),
        created_at=_iso(row["created_at"]),
        updated_at=_iso(row["updated_at"]),
    )


def _entity_outcome_from_row(row) -> EntityOutcomeRecord:
    return EntityOutcomeRecord(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        entity_id=str(row["entity_id"]),
        outcome_type=str(row["outcome_type"]),
        snooze_until=_iso(row["snooze_until"]) if row["snooze_until"] is not None else None,
        note=str(row["note"]) if row["note"] is not None else None,
        created_at=_iso(row["created_at"]),
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
        gmail_watch_history_id=str(row["gmail_watch_history_id"]) if "gmail_watch_history_id" in row and row["gmail_watch_history_id"] is not None else None,
        gmail_watch_expiration_at=_iso(row["gmail_watch_expiration_at"]) if "gmail_watch_expiration_at" in row and row["gmail_watch_expiration_at"] is not None else None,
        gmail_watch_started_at=_iso(row["gmail_watch_started_at"]) if "gmail_watch_started_at" in row and row["gmail_watch_started_at"] is not None else None,
        gmail_watch_error=str(row["gmail_watch_error"]) if "gmail_watch_error" in row and row["gmail_watch_error"] is not None else None,
        updated_at=_iso(row["updated_at"]),
    )


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)
