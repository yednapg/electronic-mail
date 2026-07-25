from __future__ import annotations

"""Postgres repositories for Gmail messages, mail groups, import state, and deletion guards."""

import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import text

from app.db.repository import get_engine
from app.db.user_mail_guard import (
    advisory_session_locks,
    client_draft_lock_key,
    gmail_draft_lock_key,
    user_mail_write_transaction,
)


# Child tables precede their parents. Authentication/account tables are deliberately
# absent: deleting Gmail-derived data must not sign the user out or delete the account.
USER_MAIL_DATA_DELETE_ORDER = (
    "app_session_snapshots",
    "gmail_client_drafts",
    "gmail_pending_sends",
    "gmail_pending_thread_actions",
    "mailbox_events",
    "gmail_thread_order_entries",
    "gmail_thread_order_state",
    "gmail_reconcile_seen",
    "gmail_initial_window_entries",
    "entity_outcomes",
    "grouping_decision_audit",
    "visible_mail_group_members",
    "visible_mail_groups",
    "mail_group_members",
    "mail_groups",
    "gmail_messages",
    "gmail_import_state",
    "background_jobs",
)


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
    body_fetch_status: str | None = None
    body_fetched_at: str | None = None
    body_fetch_error: str | None = None
    render_doc_bytes: int = 0
    ai_title: str | None = None
    ai_title_generated_at: str | None = None
    content_revision: int = 1
    attachment_descriptors: list[dict[str, Any]] = field(default_factory=list)
    attachment_descriptors_ready: bool = False
    mailbox_message_count: int | None = None
    mailbox_body_ready: bool | None = None
    mailbox_content_revision: str | None = None
    mailbox_attachment_count: int | None = None


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
    classification_version: str | None = None
    classification: dict[str, Any] | None = None
    classification_confidence: float = 0.0
    ranking_reason: str | None = None
    suppression_reason: str | None = None
    classified_at: str | None = None


@dataclass(frozen=True)
class VisibleMailGroupRecord:
    id: str
    user_id: str
    projection_key: str
    visibility: str
    group_kind: str
    status: str
    canonical_entity: str
    contact_channel: str | None
    title: str
    summary: str
    workflow_type: str
    confidence: float
    source_group_id: str | None
    source: str
    evidence: dict[str, Any]
    latest_message_at: str | None
    latest_message_id: str | None
    generated_at: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class VisibleMailGroupUpsert:
    projection_key: str
    visibility: str
    group_kind: str
    canonical_entity: str
    contact_channel: str | None
    title: str
    summary: str
    workflow_type: str
    confidence: float
    source_group_id: str | None
    source: str
    evidence: dict[str, Any]
    latest_message_at: str | None
    latest_message_id: str | None
    members: list[tuple[GmailMessageRecord, str, float]]


@dataclass(frozen=True)
class MailGroupDetail:
    group: MailGroupRecord
    messages: list[GmailMessageRecord]


@dataclass(frozen=True)
class GmailThreadSnapshotStats:
    """Lightweight sizing data used before building native offline snapshots."""

    gmail_thread_id: str
    message_count: int
    stored_bytes: int
    incomplete_body_count: int


@dataclass(frozen=True)
class GmailThreadMessagePage:
    """One bounded reader page plus whole-thread metadata.

    The message list is always limited in Postgres. The remaining fields are
    calculated over the same MVCC statement so a caller never has to load an
    oversized conversation merely to determine pagination or cache identity.
    """

    gmail_thread_id: str
    messages: list[GmailMessageRecord]
    total_messages: int
    latest_subject: str | None
    incomplete_body_count: int
    content_revision: str

    @property
    def body_ready(self) -> bool:
        return self.total_messages > 0 and self.incomplete_body_count == 0


@dataclass(frozen=True)
class MailboxThreadPage:
    threads: list[tuple[str, list[GmailMessageRecord]]]
    next_cursor: str | None
    loaded_threads: int
    order_source: str = "date"


class MailboxCursorError(ValueError):
    pass


@dataclass(frozen=True)
class _MailboxCursorState:
    mode: str
    thread_key: str
    latest_at: str | None = None
    label: str | None = None
    generation_id: str | None = None
    position: int | None = None


class _ClientDraftLockHandle:
    """Rebind a client-only create lock once Gmail returns provider identity."""

    def __init__(
        self,
        database_url: str,
        *,
        user_id: str,
        client_draft_id: str,
        gmail_draft_id: str | None,
    ) -> None:
        self.database_url = database_url
        self.user_id = user_id
        self.client_draft_id = client_draft_id
        self.gmail_draft_id = gmail_draft_id
        self._scope = None

    def acquire(self) -> None:
        lock_keys = []
        if self.gmail_draft_id:
            lock_keys.append(gmail_draft_lock_key(self.user_id, self.gmail_draft_id))
        lock_keys.append(client_draft_lock_key(self.user_id, self.client_draft_id))
        scope = advisory_session_locks(self.database_url, lock_keys=lock_keys)
        scope.__enter__()
        self._scope = scope

    def adopt_gmail_draft_id(self, gmail_draft_id: str) -> None:
        """Release client-only, then reacquire provider -> client deterministically."""
        if not gmail_draft_id or gmail_draft_id == self.gmail_draft_id:
            return
        self.release()
        self.gmail_draft_id = gmail_draft_id
        self.acquire()

    def release(self) -> None:
        scope = self._scope
        self._scope = None
        if scope is not None:
            scope.__exit__(None, None, None)


@contextmanager
def client_draft_lock(
    database_url: str,
    *,
    user_id: str,
    client_draft_id: str,
    gmail_draft_id: str | None = None,
):
    """Serialize app and provider draft identities across API replicas.

    Provider identity is always acquired before client identity. That fixed
    order lets adoption, update, send, and delete converge on one Gmail lock
    without a lock cycle, even when they use different client-side IDs.
    """
    # The helper reuses the provider-barrier session when nested, so one draft
    # operation consumes at most one slot from the bounded advisory pool.
    handle = _ClientDraftLockHandle(
        database_url,
        user_id=user_id,
        client_draft_id=client_draft_id,
        gmail_draft_id=gmail_draft_id,
    )
    handle.acquire()
    try:
        yield handle
    finally:
        handle.release()


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
class MailboxEventRecord:
    id: int
    user_id: str
    event_type: str
    mailbox_label: str | None
    payload: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class GmailImportState:
    user_id: str
    last_history_id: str | None
    history_cursor_authoritative: bool
    full_backfill_cursor: str | None
    full_backfill_started_at: str | None
    full_backfill_completed_at: str | None
    first_batch_imported_at: str | None
    first_groups_ready_at: str | None
    first_dashboard_ready_at: str | None
    last_import_started_at: str | None
    last_import_completed_at: str | None
    last_delta_sync_at: str | None
    last_sync_error: str | None
    gmail_watch_history_id: str | None
    gmail_watch_expiration_at: str | None
    gmail_watch_started_at: str | None
    gmail_watch_error: str | None
    reconcile_generation: str | None
    reconcile_cursor: str | None
    reconcile_baseline_history_id: str | None
    reconcile_started_at: str | None
    updated_at: str
    sync_generation: str | None = None
    phase: str | None = None
    initial_target_count: int = 0
    initial_metadata_count: int = 0
    initial_body_target_count: int = 0
    initial_body_ready_count: int = 0
    history_metadata_count: int = 0
    history_body_ready_count: int = 0
    estimated_total_count: int = 0
    initial_window_complete: bool = False
    history_metadata_complete: bool = False
    history_body_complete: bool = False
    last_progress_at: str | None = None
    attachment_descriptors_complete: bool = False


@dataclass(frozen=True)
class GmailInitialWindowEntry:
    user_id: str
    generation_id: str
    gmail_thread_id: str
    position: int
    message_count: int
    metadata_ready_at: str | None
    body_ready_at: str | None


@dataclass(frozen=True)
class GmailSyncProgress:
    sync_generation: str | None = None
    phase: str | None = None
    initial_target_count: int = 0
    initial_metadata_count: int = 0
    initial_body_target_count: int = 0
    initial_body_ready_count: int = 0
    history_metadata_count: int = 0
    history_body_ready_count: int = 0
    estimated_total_count: int = 0
    initial_window_complete: bool = False
    history_metadata_complete: bool = False
    history_body_complete: bool = False
    last_progress_at: str | None = None


class GmailBodyUpdateResult(list[str]):
    """List-compatible body result carrying the same-transaction progress."""

    def __init__(self, message_ids: Iterable[str], progress: GmailSyncProgress | None) -> None:
        super().__init__(message_ids)
        self.progress = progress


@dataclass(frozen=True)
class GmailReconcileFinalizeResult:
    deleted_message_count: int
    affected_group_ids: list[str]
    progress: GmailSyncProgress | None = None


def gmail_history_cursor_is_authoritative(state: Any) -> bool:
    """Return whether a cursor has durable fully-consumed-history provenance."""
    return bool(
        state
        and getattr(state, "history_cursor_authoritative", False)
        and str(getattr(state, "last_history_id", "") or "").isdigit()
    )


@dataclass(frozen=True)
class AppSessionSnapshotRecord:
    user_id: str
    dashboard: dict[str, Any]
    mailbox: dict[str, Any]
    sync: dict[str, Any]
    updated_at: str


@dataclass(frozen=True)
class PendingThreadActionRecord:
    server_action_id: str
    client_action_id: str
    user_id: str
    mailbox_thread_id: str
    target_message_id: str | None
    action: str
    state: str
    created_at: str
    queued_at: str
    applied_at: str | None
    error: str | None
    updated_at: str
    previous_labels: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class PendingSendRecord:
    server_send_id: str
    client_send_id: str
    user_id: str
    send_type: str
    mailbox_thread_id: str | None
    gmail_thread_id: str | None
    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    body_text: str
    body_html: str | None
    headers: dict[str, Any]
    state: str
    created_at: str
    queued_at: str
    sent_at: str | None
    gmail_message_id: str | None
    error: str | None
    updated_at: str
    attachments: list[dict[str, str]] = field(default_factory=list)
    request_hash: str = ""


class MailSendIdempotencyConflict(RuntimeError):
    """A client send identity was reused for a different immutable request."""


@dataclass(frozen=True)
class ClientDraftRecord:
    user_id: str
    client_draft_id: str
    gmail_draft_id: str | None
    gmail_message_id: str | None
    gmail_thread_id: str | None
    content_hash: str
    state: str
    last_client_send_id: str | None
    sent_message_id: str | None
    error: str | None
    created_at: str
    saved_at: str | None
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


def list_connected_gmail_user_ids(database_url: str) -> list[str]:
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT users.id
                FROM users
                JOIN google_oauth_tokens tokens ON tokens.user_id = users.id
                WHERE users.access_enabled = TRUE
                  AND users.google_disconnected_at IS NULL
                  AND users.google_data_delete_requested_at IS NULL
                  AND users.google_data_deleted_at IS NULL
                ORDER BY users.updated_at DESC
                """
            )
        ).mappings().all()
    return [str(row["id"]) for row in rows]


def clear_google_guard_state(
    database_url: str,
    *,
    user_id: str,
    oauth_started_epoch: int,
) -> None:
    with get_engine(database_url).begin() as connection:
        connection.execute(
            text(
                "SELECT set_config('electronic_mail.oauth_started_epoch', :oauth_started_epoch, TRUE)"
            ),
            {"oauth_started_epoch": str(oauth_started_epoch)},
        )
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
        for table in USER_MAIL_DATA_DELETE_ORDER:
            if table == "entity_outcomes":
                # Outcomes attached to manual tasks are app-owned rather than Gmail-derived.
                # Everything else can reference a raw Gmail thread, deterministic cluster,
                # mail group, or visible group and must be purged with mailbox data.
                connection.execute(
                    text(
                        """
                        DELETE FROM entity_outcomes AS outcome
                        WHERE outcome.user_id = :user_id
                          AND NOT EXISTS (
                            SELECT 1
                            FROM manual_tasks AS task
                            WHERE task.user_id = outcome.user_id
                              AND task.entity_id = outcome.entity_id
                          )
                        """
                    ),
                    {"user_id": user_id},
                )
                continue
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
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        connection.execute(
            text(
                """
                INSERT INTO gmail_import_state (user_id, last_import_started_at, updated_at)
                VALUES (:user_id, now(), now())
                ON CONFLICT (user_id) DO UPDATE SET
                  last_import_started_at = now(),
                  phase = CASE
                    WHEN gmail_import_state.sync_generation IS NOT NULL
                      AND gmail_import_state.history_metadata_complete
                      THEN 'syncing_recent'
                    ELSE gmail_import_state.phase
                  END,
                  last_progress_at = CASE
                    WHEN gmail_import_state.sync_generation IS NOT NULL THEN now()
                    ELSE gmail_import_state.last_progress_at
                  END,
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
    full_backfill_cursor: str | None = None,
    clear_full_backfill_cursor: bool = False,
    full_backfill_started: bool = False,
    full_backfill_completed: bool = False,
) -> None:
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        connection.execute(
            text(
                """
                INSERT INTO gmail_import_state (
                  user_id, full_backfill_cursor, full_backfill_started_at,
                  full_backfill_completed_at, first_batch_imported_at,
                  first_groups_ready_at, first_dashboard_ready_at, last_import_completed_at, updated_at
                ) VALUES (
                  :user_id, :full_backfill_cursor,
                  CASE WHEN :full_backfill_started THEN now() ELSE NULL END,
                  CASE WHEN :full_backfill_completed THEN now() ELSE NULL END,
                  CASE WHEN :first_batch THEN now() ELSE NULL END,
                  CASE WHEN :groups_ready THEN now() ELSE NULL END,
                  CASE WHEN :dashboard_ready THEN now() ELSE NULL END,
                  now(), now()
                )
                ON CONFLICT (user_id) DO UPDATE SET
                  full_backfill_cursor = CASE
                    WHEN :clear_full_backfill_cursor THEN NULL
                    ELSE COALESCE(excluded.full_backfill_cursor, gmail_import_state.full_backfill_cursor)
                  END,
                  full_backfill_started_at = COALESCE(gmail_import_state.full_backfill_started_at, excluded.full_backfill_started_at),
                  full_backfill_completed_at = CASE
                    WHEN :full_backfill_completed THEN COALESCE(excluded.full_backfill_completed_at, now())
                    WHEN excluded.full_backfill_cursor IS NOT NULL THEN NULL
                    ELSE gmail_import_state.full_backfill_completed_at
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
                "full_backfill_cursor": full_backfill_cursor,
                "clear_full_backfill_cursor": clear_full_backfill_cursor,
                "full_backfill_started": full_backfill_started,
                "full_backfill_completed": full_backfill_completed,
                "first_batch": first_batch,
                "groups_ready": groups_ready,
                "dashboard_ready": dashboard_ready,
            },
        )


def mark_history_delta_completed(
    database_url: str,
    *,
    user_id: str,
    last_history_id: str,
) -> None:
    """Publish a cursor only after its Gmail history traversal fully completed."""
    last_history_id = last_history_id.strip()
    if not last_history_id.isdigit():
        raise ValueError("Gmail history ID must be numeric")
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        connection.execute(
            text(
                """
                INSERT INTO gmail_import_state (
                  user_id, last_history_id, last_import_completed_at,
                  last_delta_sync_at, history_cursor_authoritative, updated_at
                ) VALUES (
                  :user_id, :last_history_id, now(), now(), TRUE, now()
                )
                ON CONFLICT (user_id) DO UPDATE SET
                  last_history_id = CASE
                    WHEN gmail_import_state.history_cursor_authoritative IS NOT TRUE
                      THEN excluded.last_history_id
                    WHEN gmail_import_state.last_history_id IS NULL
                      OR gmail_import_state.last_history_id !~ '^[0-9]+$'
                      THEN excluded.last_history_id
                    WHEN excluded.last_history_id::NUMERIC >= gmail_import_state.last_history_id::NUMERIC
                      THEN excluded.last_history_id
                    ELSE gmail_import_state.last_history_id
                  END,
                  last_import_completed_at = now(),
                  last_delta_sync_at = now(),
                  history_cursor_authoritative = TRUE,
                  phase = CASE
                    WHEN gmail_import_state.sync_generation IS NULL THEN gmail_import_state.phase
                    WHEN gmail_import_state.history_body_complete THEN 'complete'
                    ELSE 'usable'
                  END,
                  last_progress_at = CASE
                    WHEN gmail_import_state.sync_generation IS NOT NULL THEN now()
                    ELSE gmail_import_state.last_progress_at
                  END,
                  last_sync_error = NULL,
                  updated_at = now()
                """
            ),
            {"user_id": user_id, "last_history_id": last_history_id},
        )


def mark_import_error(database_url: str, *, user_id: str, error: str) -> None:
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        connection.execute(
            text(
                """
                INSERT INTO gmail_import_state (user_id, last_sync_error, updated_at)
                VALUES (:user_id, :error, now())
                ON CONFLICT (user_id) DO UPDATE SET
                  last_sync_error = excluded.last_sync_error,
                  phase = CASE
                    WHEN gmail_import_state.sync_generation IS NOT NULL THEN 'failed'
                    ELSE gmail_import_state.phase
                  END,
                  last_progress_at = CASE
                    WHEN gmail_import_state.sync_generation IS NOT NULL THEN now()
                    ELSE gmail_import_state.last_progress_at
                  END,
                  updated_at = now()
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
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
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
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
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


def gmail_sync_progress(state: GmailImportState | None) -> GmailSyncProgress:
    if state is None:
        return GmailSyncProgress()
    return GmailSyncProgress(
        sync_generation=getattr(state, "sync_generation", None),
        phase=getattr(state, "phase", None),
        initial_target_count=int(getattr(state, "initial_target_count", 0) or 0),
        initial_metadata_count=int(getattr(state, "initial_metadata_count", 0) or 0),
        initial_body_target_count=int(getattr(state, "initial_body_target_count", 0) or 0),
        initial_body_ready_count=int(getattr(state, "initial_body_ready_count", 0) or 0),
        history_metadata_count=int(getattr(state, "history_metadata_count", 0) or 0),
        history_body_ready_count=int(getattr(state, "history_body_ready_count", 0) or 0),
        estimated_total_count=int(getattr(state, "estimated_total_count", 0) or 0),
        initial_window_complete=bool(getattr(state, "initial_window_complete", False)),
        history_metadata_complete=bool(getattr(state, "history_metadata_complete", False)),
        history_body_complete=bool(getattr(state, "history_body_complete", False)),
        last_progress_at=getattr(state, "last_progress_at", None),
    )


def activate_existing_gmail_progressive_sync(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    history_metadata_complete: bool,
) -> GmailImportState | None:
    """Adopt a pre-progressive mailbox without discarding durable import state.

    Existing accounts already have useful Gmail rows (and may still own a
    resumable legacy backfill cursor).  Activation therefore derives the
    progressive counters and initial window from those rows in one guarded
    transaction.  It deliberately never writes Gmail cursors, reconciliation
    state, or legacy completion timestamps.
    """
    generation_id = generation_id.strip()
    if not generation_id:
        raise ValueError("Gmail sync generation is required")
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        state = connection.execute(
            text(
                """
                SELECT *
                FROM gmail_import_state
                WHERE user_id = :user_id
                FOR UPDATE
                """
            ),
            {"user_id": user_id},
        ).mappings().first()
        if state is None:
            return None
        if state["sync_generation"] is not None or state["first_batch_imported_at"] is None:
            return _state_from_row(state)

        activated = connection.execute(
            text(
                """
                UPDATE gmail_import_state
                SET sync_generation = :generation_id,
                    phase = 'usable',
                    last_progress_at = now(),
                    updated_at = now()
                WHERE user_id = :user_id
                  AND sync_generation IS NULL
                  AND first_batch_imported_at IS NOT NULL
                RETURNING user_id
                """
            ),
            {"user_id": user_id, "generation_id": generation_id},
        ).scalar_one_or_none()
        if activated is None:
            current = connection.execute(
                text("SELECT * FROM gmail_import_state WHERE user_id = :user_id"),
                {"user_id": user_id},
            ).mappings().first()
            return _state_from_row(current) if current is not None else None

        connection.execute(
            text(
                """
                WITH ranked_threads AS (
                  SELECT
                    messages.gmail_thread_id,
                    COUNT(*)::INTEGER AS message_count,
                    BOOL_AND(
                      COALESCE(messages.body_fetch_status, 'missing') IN ('fetched', 'unavailable')
                    ) AS body_ready,
                    ROW_NUMBER() OVER (
                      ORDER BY MAX(messages.internal_date) DESC NULLS LAST,
                               messages.gmail_thread_id DESC
                    ) - 1 AS position
                  FROM gmail_messages AS messages
                  WHERE messages.user_id = :user_id
                    AND NULLIF(messages.gmail_thread_id, '') IS NOT NULL
                  GROUP BY messages.gmail_thread_id
                )
                INSERT INTO gmail_initial_window_entries (
                  user_id, generation_id, gmail_thread_id, position,
                  message_count, metadata_ready_at, body_ready_at,
                  created_at, updated_at
                )
                SELECT
                  :user_id, :generation_id, ranked.gmail_thread_id, ranked.position,
                  ranked.message_count, now(),
                  CASE WHEN ranked.body_ready THEN now() ELSE NULL END,
                  now(), now()
                FROM ranked_threads AS ranked
                WHERE ranked.position < 100
                ON CONFLICT (user_id, generation_id, gmail_thread_id) DO NOTHING
                """
            ),
            {"user_id": user_id, "generation_id": generation_id},
        )
        counts = connection.execute(
            text(
                """
                SELECT
                  (
                    SELECT COUNT(*)::INTEGER
                    FROM gmail_initial_window_entries AS entries
                    WHERE entries.user_id = :user_id
                      AND entries.generation_id = :generation_id
                  ) AS initial_count,
                  (
                    SELECT COUNT(*)::INTEGER
                    FROM gmail_initial_window_entries AS entries
                    WHERE entries.user_id = :user_id
                      AND entries.generation_id = :generation_id
                      AND entries.position < 25
                      AND entries.body_ready_at IS NOT NULL
                  ) AS initial_body_ready_count,
                  (
                    SELECT COUNT(DISTINCT COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id))::INTEGER
                    FROM gmail_messages AS messages
                    WHERE messages.user_id = :user_id
                  ) AS history_count,
                  (
                    SELECT COUNT(*)::INTEGER
                    FROM (
                      SELECT COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) AS thread_key
                      FROM gmail_messages AS messages
                      WHERE messages.user_id = :user_id
                      GROUP BY thread_key
                      HAVING BOOL_AND(
                        COALESCE(messages.body_fetch_status, 'missing') IN ('fetched', 'unavailable')
                      )
                    ) AS ready_threads
                  ) AS history_body_ready_count,
                  NOT EXISTS (
                    SELECT 1
                    FROM gmail_messages AS messages
                    WHERE messages.user_id = :user_id
                      AND COALESCE(messages.body_fetch_status, 'missing') NOT IN ('fetched', 'unavailable')
                  ) AS all_bodies_ready
                """
            ),
            {"user_id": user_id, "generation_id": generation_id},
        ).mappings().one()
        initial_count = int(counts["initial_count"] or 0)
        history_count = int(counts["history_count"] or 0)
        # Re-read completion under the row lock: a reconciliation or legacy
        # backfill may have completed after the caller's initial state read.
        metadata_complete = bool(
            history_metadata_complete or state.get("full_backfill_completed_at")
        )
        bodies_complete = metadata_complete and bool(counts["all_bodies_ready"])
        row = connection.execute(
            text(
                """
                UPDATE gmail_import_state
                SET initial_target_count = :initial_count,
                    initial_metadata_count = :initial_count,
                    initial_body_target_count = LEAST(25, :initial_count),
                    initial_body_ready_count = LEAST(
                      LEAST(25, :initial_count),
                      :initial_body_ready_count
                    ),
                    history_metadata_count = :history_count,
                    history_body_ready_count = LEAST(:history_count, :history_body_ready_count),
                    estimated_total_count = GREATEST(estimated_total_count, :history_count),
                    initial_window_complete = TRUE,
                    history_metadata_complete = :history_metadata_complete,
                    history_body_complete = :history_body_complete,
                    phase = CASE
                      WHEN :history_body_complete THEN 'complete'
                      WHEN :history_metadata_complete THEN 'syncing_history'
                      ELSE 'usable'
                    END,
                    last_progress_at = now(),
                    updated_at = now()
                WHERE user_id = :user_id
                  AND sync_generation = :generation_id
                RETURNING *
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "initial_count": initial_count,
                "initial_body_ready_count": int(counts["initial_body_ready_count"] or 0),
                "history_count": history_count,
                "history_body_ready_count": int(counts["history_body_ready_count"] or 0),
                "history_metadata_complete": metadata_complete,
                "history_body_complete": bodies_complete,
            },
        ).mappings().first()
    return _state_from_row(row) if row is not None else None


def start_gmail_sync_progress(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
) -> GmailImportState | None:
    """Fence progressive bootstrap writes to the active reconciliation generation."""
    generation_id = generation_id.strip()
    if not generation_id:
        raise ValueError("Gmail sync generation is required")
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        row = connection.execute(
            text(
                """
                UPDATE gmail_import_state
                SET sync_generation = :generation_id,
                    phase = CASE
                      WHEN sync_generation = :generation_id AND phase IS NOT NULL
                        THEN phase
                      ELSE 'discovering_recent'
                    END,
                    initial_target_count = CASE WHEN sync_generation = :generation_id THEN initial_target_count ELSE 0 END,
                    initial_metadata_count = CASE WHEN sync_generation = :generation_id THEN initial_metadata_count ELSE 0 END,
                    initial_body_target_count = CASE WHEN sync_generation = :generation_id THEN initial_body_target_count ELSE 0 END,
                    initial_body_ready_count = CASE WHEN sync_generation = :generation_id THEN initial_body_ready_count ELSE 0 END,
                    history_metadata_count = CASE WHEN sync_generation = :generation_id THEN history_metadata_count ELSE 0 END,
                    history_body_ready_count = CASE WHEN sync_generation = :generation_id THEN history_body_ready_count ELSE 0 END,
                    estimated_total_count = CASE WHEN sync_generation = :generation_id THEN estimated_total_count ELSE 0 END,
                    initial_window_complete = CASE WHEN sync_generation = :generation_id THEN initial_window_complete ELSE FALSE END,
                    history_metadata_complete = CASE WHEN sync_generation = :generation_id THEN history_metadata_complete ELSE FALSE END,
                    history_body_complete = CASE WHEN sync_generation = :generation_id THEN history_body_complete ELSE FALSE END,
                    last_progress_at = CASE
                      WHEN sync_generation = :generation_id
                        THEN COALESCE(last_progress_at, now())
                      ELSE now()
                    END,
                    updated_at = now()
                WHERE user_id = :user_id
                  AND reconcile_generation = :generation_id
                RETURNING *
                """
            ),
            {"user_id": user_id, "generation_id": generation_id},
        ).mappings().first()
        if row is None:
            return None
        connection.execute(
            text(
                """
                DELETE FROM gmail_initial_window_entries
                WHERE user_id = :user_id
                  AND generation_id <> :generation_id
                """
            ),
            {"user_id": user_id, "generation_id": generation_id},
        )
    return _state_from_row(row)


def initialize_gmail_initial_window(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    gmail_thread_ids: list[str],
    estimated_total_count: int = 0,
) -> GmailSyncProgress | None:
    """Persist one capped provider discovery response exactly once."""
    unique_thread_ids = list(dict.fromkeys(thread_id for thread_id in gmail_thread_ids if thread_id))[:100]
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        active = connection.execute(
            text(
                """
                SELECT 1
                FROM gmail_import_state
                WHERE user_id = :user_id
                  AND sync_generation = :generation_id
                FOR UPDATE
                """
            ),
            {"user_id": user_id, "generation_id": generation_id},
        ).scalar_one_or_none()
        if active is None:
            return None
        already_discovered = connection.execute(
            text(
                """
                SELECT EXISTS (
                  SELECT 1
                  FROM gmail_initial_window_entries
                  WHERE user_id = :user_id
                    AND generation_id = :generation_id
                ) OR EXISTS (
                  SELECT 1
                  FROM gmail_import_state
                  WHERE user_id = :user_id
                    AND sync_generation = :generation_id
                    AND phase IS NOT NULL
                    AND phase NOT IN ('discovering_recent', 'failed')
                )
                """
            ),
            {"user_id": user_id, "generation_id": generation_id},
        ).scalar_one()
        if not already_discovered:
            for position, thread_id in enumerate(unique_thread_ids):
                connection.execute(
                    text(
                        """
                        INSERT INTO gmail_initial_window_entries (
                          user_id, generation_id, gmail_thread_id, position,
                          created_at, updated_at
                        ) VALUES (
                          :user_id, :generation_id, :gmail_thread_id, :position,
                          now(), now()
                        )
                        ON CONFLICT (user_id, generation_id, gmail_thread_id) DO NOTHING
                        """
                    ),
                    {
                        "user_id": user_id,
                        "generation_id": generation_id,
                        "gmail_thread_id": thread_id,
                        "position": position,
                    },
                )
            target_count = len(unique_thread_ids)
            connection.execute(
                text(
                    """
                    UPDATE gmail_import_state
                    SET initial_target_count = :target_count,
                        initial_body_target_count = LEAST(25, :target_count),
                        estimated_total_count = GREATEST(estimated_total_count, :estimated_total_count),
                        initial_window_complete = (:target_count = 0),
                        first_batch_imported_at = CASE
                          WHEN :target_count = 0 THEN COALESCE(first_batch_imported_at, now())
                          ELSE first_batch_imported_at
                        END,
                        phase = CASE
                          WHEN :target_count = 0 THEN 'syncing_history'
                          ELSE 'importing_metadata'
                        END,
                        last_import_completed_at = CASE WHEN :target_count = 0 THEN now() ELSE last_import_completed_at END,
                        last_progress_at = now(),
                        last_sync_error = NULL,
                        updated_at = now()
                    WHERE user_id = :user_id
                      AND sync_generation = :generation_id
                    """
                ),
                {
                    "user_id": user_id,
                    "generation_id": generation_id,
                    "target_count": target_count,
                    "estimated_total_count": max(0, int(estimated_total_count)),
                },
            )
        row = connection.execute(
            text("SELECT * FROM gmail_import_state WHERE user_id = :user_id"),
            {"user_id": user_id},
        ).mappings().one()
    return gmail_sync_progress(_state_from_row(row))


def list_pending_gmail_initial_window_entries(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    limit: int = 25,
) -> list[GmailInitialWindowEntry]:
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT *
                FROM gmail_initial_window_entries
                WHERE user_id = :user_id
                  AND generation_id = :generation_id
                  AND metadata_ready_at IS NULL
                ORDER BY position ASC
                LIMIT :limit
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "limit": max(1, min(int(limit), 25)),
            },
        ).mappings().all()
    return [_initial_window_entry_from_row(row) for row in rows]


def list_gmail_initial_window_entries_needing_body_fetch(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    limit: int = 100,
) -> list[GmailInitialWindowEntry]:
    """Recover committed initial metadata whose body job is not yet terminal."""
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT *
                FROM gmail_initial_window_entries
                WHERE user_id = :user_id
                  AND generation_id = :generation_id
                  AND metadata_ready_at IS NOT NULL
                  AND body_ready_at IS NULL
                ORDER BY position ASC
                LIMIT :limit
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "limit": max(1, min(int(limit), 100)),
            },
        ).mappings().all()
    return [_initial_window_entry_from_row(row) for row in rows]


def list_gmail_initial_window_positions(
    database_url: str,
    *,
    user_id: str,
    gmail_thread_ids: list[str],
) -> dict[str, int]:
    """Return ranks from only the account's active progressive generation."""
    normalized = list(dict.fromkeys(thread_id for thread_id in gmail_thread_ids if thread_id))
    if not normalized:
        return {}
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT entries.gmail_thread_id, entries.position
                FROM gmail_initial_window_entries AS entries
                JOIN gmail_import_state AS state
                  ON state.user_id = entries.user_id
                 AND state.sync_generation = entries.generation_id
                WHERE entries.user_id = :user_id
                  AND entries.gmail_thread_id = ANY(:thread_ids)
                """
            ),
            {"user_id": user_id, "thread_ids": normalized},
        ).mappings().all()
    return {str(row["gmail_thread_id"]): int(row["position"]) for row in rows}


def commit_gmail_initial_window_metadata_batch(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    gmail_thread_ids: list[str],
    messages: Iterable[GmailMessageRecord],
    terminal_missing_thread_ids: list[str] | None = None,
) -> GmailSyncProgress | None:
    """Atomically publish at most 25 conversations and monotonic progress."""
    unique_thread_ids = list(dict.fromkeys(thread_id for thread_id in gmail_thread_ids if thread_id))
    terminal_thread_ids = list(
        dict.fromkeys(
            thread_id
            for thread_id in (terminal_missing_thread_ids or [])
            if thread_id
        )
    )
    if set(unique_thread_ids) & set(terminal_thread_ids):
        raise ValueError("A Gmail thread cannot be both hydrated and terminally missing")
    if len(set(unique_thread_ids) | set(terminal_thread_ids)) > 25:
        raise ValueError("An initial Gmail metadata commit is limited to 25 threads")
    rows = list(messages)
    if any(message.user_id != user_id for message in rows):
        raise ValueError("Initial Gmail metadata belongs to a different user")
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        active = connection.execute(
            text(
                """
                SELECT 1
                FROM gmail_import_state
                WHERE user_id = :user_id
                  AND sync_generation = :generation_id
                FOR UPDATE
                """
            ),
            {"user_id": user_id, "generation_id": generation_id},
        ).scalar_one_or_none()
        if active is None:
            return None
        if terminal_thread_ids:
            connection.execute(
                text(
                    """
                    UPDATE gmail_initial_window_entries
                    SET metadata_ready_at = COALESCE(metadata_ready_at, now()),
                        body_ready_at = COALESCE(body_ready_at, now()),
                        message_count = 0,
                        updated_at = now()
                    WHERE user_id = :user_id
                      AND generation_id = :generation_id
                      AND gmail_thread_id = ANY(:thread_ids)
                    """
                ),
                {
                    "user_id": user_id,
                    "generation_id": generation_id,
                    "thread_ids": terminal_thread_ids,
                },
            )
        _upsert_gmail_messages_on_connection(connection, rows)
        if unique_thread_ids:
            connection.execute(
                text(
                    """
                    UPDATE gmail_initial_window_entries AS entries
                    SET metadata_ready_at = COALESCE(entries.metadata_ready_at, now()),
                        message_count = counts.message_count,
                        body_ready_at = CASE
                          WHEN counts.message_count > 0
                            AND counts.missing_body_count = 0
                            THEN COALESCE(entries.body_ready_at, now())
                          ELSE entries.body_ready_at
                        END,
                        updated_at = now()
                    FROM (
                      SELECT listed.gmail_thread_id,
                             COUNT(messages.message_id)::INTEGER AS message_count,
                             COUNT(messages.message_id) FILTER (
                               WHERE COALESCE(messages.body_fetch_status, 'missing') NOT IN ('fetched', 'unavailable')
                             )::INTEGER AS missing_body_count
                      FROM unnest(CAST(:thread_ids AS TEXT[])) AS listed(gmail_thread_id)
                      LEFT JOIN gmail_messages AS messages
                        ON messages.user_id = :user_id
                       AND COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) = listed.gmail_thread_id
                      GROUP BY listed.gmail_thread_id
                    ) AS counts
                    WHERE entries.user_id = :user_id
                      AND entries.generation_id = :generation_id
                      AND entries.gmail_thread_id = counts.gmail_thread_id
                    """
                ),
                {
                    "user_id": user_id,
                    "generation_id": generation_id,
                    "thread_ids": unique_thread_ids,
                },
            )
        counts = connection.execute(
            text(
                """
                SELECT COUNT(*) FILTER (WHERE metadata_ready_at IS NOT NULL)::INTEGER AS metadata_count,
                       COUNT(*) FILTER (
                         WHERE body_ready_at IS NOT NULL
                           AND position < 25
                       )::INTEGER AS body_ready_count,
                       COUNT(*)::INTEGER AS target_count
                FROM gmail_initial_window_entries
                WHERE user_id = :user_id
                  AND generation_id = :generation_id
                """
            ),
            {"user_id": user_id, "generation_id": generation_id},
        ).mappings().one()
        metadata_count = int(counts["metadata_count"] or 0)
        body_ready_count = int(counts["body_ready_count"] or 0)
        target_count = int(counts["target_count"] or 0)
        complete = metadata_count >= target_count
        row = connection.execute(
            text(
                """
                UPDATE gmail_import_state
                SET initial_target_count = GREATEST(initial_target_count, :target_count),
                    initial_metadata_count = GREATEST(initial_metadata_count, :metadata_count),
                    initial_body_target_count = GREATEST(initial_body_target_count, LEAST(25, :target_count)),
                    initial_body_ready_count = GREATEST(initial_body_ready_count, :body_ready_count),
                    initial_window_complete = initial_window_complete OR :complete,
                    first_batch_imported_at = CASE
                      WHEN :metadata_count > 0 OR :target_count = 0
                        THEN COALESCE(first_batch_imported_at, now())
                      ELSE first_batch_imported_at
                    END,
                    phase = CASE
                      WHEN :complete AND :body_ready_count >= LEAST(25, :target_count)
                        THEN 'usable'
                      WHEN :complete THEN 'hydrating_priority_content'
                      ELSE 'importing_metadata'
                    END,
                    last_import_completed_at = now(),
                    last_progress_at = now(),
                    last_sync_error = NULL,
                    updated_at = now()
                WHERE user_id = :user_id
                  AND sync_generation = :generation_id
                RETURNING *
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "target_count": target_count,
                "metadata_count": metadata_count,
                "body_ready_count": body_ready_count,
                "complete": complete,
            },
        ).mappings().first()
    return gmail_sync_progress(_state_from_row(row)) if row is not None else None


def start_gmail_reconciliation(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    baseline_history_id: str,
    initial_cursor: str,
) -> GmailImportState:
    """Start one durable authoritative scan, or return the already-active scan."""
    generation_id = generation_id.strip()
    baseline_history_id = baseline_history_id.strip()
    if not generation_id or not initial_cursor:
        raise ValueError("Gmail reconciliation identity and cursor are required")
    if not baseline_history_id.isdigit():
        raise ValueError("Gmail reconciliation history ID must be numeric")
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        connection.execute(
            text(
                """
                INSERT INTO gmail_import_state (user_id, updated_at)
                VALUES (:user_id, now())
                ON CONFLICT (user_id) DO NOTHING
                """
            ),
            {"user_id": user_id},
        )
        row = connection.execute(
            text(
                """
                UPDATE gmail_import_state
                SET reconcile_generation = :generation_id,
                    reconcile_cursor = :initial_cursor,
                    reconcile_baseline_history_id = :baseline_history_id,
                    reconcile_started_at = now(),
                    history_cursor_authoritative = FALSE,
                    last_import_started_at = now(),
                    last_sync_error = NULL,
                    updated_at = now()
                WHERE user_id = :user_id
                  AND reconcile_generation IS NULL
                RETURNING *
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "initial_cursor": initial_cursor,
                "baseline_history_id": baseline_history_id,
            },
        ).mappings().first()
        if row is None:
            row = connection.execute(
                text("SELECT * FROM gmail_import_state WHERE user_id = :user_id FOR UPDATE"),
                {"user_id": user_id},
            ).mappings().one()
        active_generation = str(row["reconcile_generation"] or "")
        connection.execute(
            text(
                """
                DELETE FROM gmail_reconcile_seen
                WHERE user_id = :user_id
                  AND generation_id <> :active_generation
                """
            ),
            {"user_id": user_id, "active_generation": active_generation},
        )
    return _state_from_row(row)


def record_gmail_reconciliation_page(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    expected_cursor: str,
    next_cursor: str | None,
    message_ids: list[str],
) -> bool:
    """Atomically checkpoint a page and its authoritative remote identities."""
    unique_message_ids = list(dict.fromkeys(message_id for message_id in message_ids if message_id))
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        advanced = connection.execute(
            text(
                """
                UPDATE gmail_import_state
                SET reconcile_cursor = :next_cursor,
                    last_import_completed_at = now(),
                    last_sync_error = NULL,
                    updated_at = now()
                WHERE user_id = :user_id
                  AND reconcile_generation = :generation_id
                  AND reconcile_cursor IS NOT DISTINCT FROM :expected_cursor
                RETURNING user_id
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "expected_cursor": expected_cursor,
                "next_cursor": next_cursor,
            },
        ).scalar_one_or_none()
        if advanced is None:
            return False
        if unique_message_ids:
            connection.execute(
                text(
                    """
                    INSERT INTO gmail_reconcile_seen (user_id, generation_id, message_id, seen_at)
                    SELECT :user_id, :generation_id, listed.message_id, now()
                    FROM unnest(CAST(:message_ids AS TEXT[])) AS listed(message_id)
                    ON CONFLICT (user_id, generation_id, message_id) DO NOTHING
                    """
                ),
                {
                    "user_id": user_id,
                    "generation_id": generation_id,
                    "message_ids": unique_message_ids,
                },
            )
    return True


def commit_gmail_reconciliation_metadata_page(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    expected_cursor: str,
    next_cursor: str | None,
    messages: Iterable[GmailMessageRecord],
    conversation_count: int,
    estimated_total_count: int = 0,
) -> GmailSyncProgress | None:
    """Commit one independently retryable 100-conversation history page."""
    conversation_count = max(0, int(conversation_count))
    if conversation_count > 100:
        raise ValueError("A Gmail history metadata page is limited to 100 conversations")
    rows = list(messages)
    if any(message.user_id != user_id for message in rows):
        raise ValueError("Gmail reconciliation metadata belongs to a different user")
    message_ids = list(dict.fromkeys(message.message_id for message in rows if message.message_id))
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        advanced = connection.execute(
            text(
                """
                UPDATE gmail_import_state
                SET reconcile_cursor = :next_cursor,
                    phase = CASE
                      WHEN phase IN ('complete', 'failed') THEN phase
                      WHEN initial_window_complete
                        AND initial_body_ready_count >= LEAST(25, initial_body_target_count)
                        THEN 'syncing_history'
                      ELSE phase
                    END,
                    estimated_total_count = GREATEST(estimated_total_count, :estimated_total_count),
                    last_import_completed_at = now(),
                    last_progress_at = now(),
                    last_sync_error = NULL,
                    updated_at = now()
                WHERE user_id = :user_id
                  AND reconcile_generation = :generation_id
                  AND sync_generation = :generation_id
                  AND reconcile_cursor IS NOT DISTINCT FROM :expected_cursor
                RETURNING user_id
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "expected_cursor": expected_cursor,
                "next_cursor": next_cursor,
                "estimated_total_count": max(0, int(estimated_total_count)),
            },
        ).scalar_one_or_none()
        if advanced is None:
            return None
        _upsert_gmail_messages_on_connection(connection, rows)
        if message_ids:
            connection.execute(
                text(
                    """
                    INSERT INTO gmail_reconcile_seen (user_id, generation_id, message_id, seen_at)
                    SELECT :user_id, :generation_id, listed.message_id, now()
                    FROM unnest(CAST(:message_ids AS TEXT[])) AS listed(message_id)
                    ON CONFLICT (user_id, generation_id, message_id) DO NOTHING
                    """
                ),
                {
                    "user_id": user_id,
                    "generation_id": generation_id,
                    "message_ids": message_ids,
                },
            )
        metadata_count = connection.execute(
            text(
                """
                SELECT COUNT(DISTINCT COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id))::INTEGER
                FROM gmail_reconcile_seen AS seen
                JOIN gmail_messages AS messages
                  ON messages.user_id = seen.user_id
                 AND messages.message_id = seen.message_id
                WHERE seen.user_id = :user_id
                  AND seen.generation_id = :generation_id
                """
            ),
            {"user_id": user_id, "generation_id": generation_id},
        ).scalar_one()
        row = connection.execute(
            text(
                """
                UPDATE gmail_import_state
                SET history_metadata_count = GREATEST(history_metadata_count, :metadata_count),
                    last_progress_at = now(),
                    updated_at = now()
                WHERE user_id = :user_id
                  AND sync_generation = :generation_id
                RETURNING *
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "metadata_count": max(int(metadata_count or 0), conversation_count),
            },
        ).mappings().first()
    return gmail_sync_progress(_state_from_row(row)) if row is not None else None


def record_gmail_reconciliation_seen(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    message_ids: list[str],
) -> bool:
    """Protect final-delta messages from the absent-message sweep."""
    unique_message_ids = list(dict.fromkeys(message_id for message_id in message_ids if message_id))
    if not unique_message_ids:
        return True
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        active = connection.execute(
            text(
                """
                SELECT 1
                FROM gmail_import_state
                WHERE user_id = :user_id
                  AND reconcile_generation = :generation_id
                  AND reconcile_cursor IS NULL
                """
            ),
            {"user_id": user_id, "generation_id": generation_id},
        ).scalar_one_or_none()
        if active is None:
            return False
        connection.execute(
            text(
                """
                INSERT INTO gmail_reconcile_seen (user_id, generation_id, message_id, seen_at)
                SELECT :user_id, :generation_id, listed.message_id, now()
                FROM unnest(CAST(:message_ids AS TEXT[])) AS listed(message_id)
                ON CONFLICT (user_id, generation_id, message_id) DO NOTHING
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "message_ids": unique_message_ids,
            },
        )
    return True


def finalize_gmail_reconciliation(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
    final_history_id: str,
) -> GmailReconcileFinalizeResult | None:
    """Delete only authoritatively absent rows and atomically publish the new cursor."""
    final_history_id = final_history_id.strip()
    if not final_history_id.isdigit():
        raise ValueError("Gmail reconciliation history ID must be numeric")
    missing_predicate = """
        messages.user_id = :user_id
        AND NOT EXISTS (
          SELECT 1
          FROM gmail_reconcile_seen AS seen
          WHERE seen.user_id = messages.user_id
            AND seen.generation_id = :generation_id
            AND seen.message_id = messages.message_id
        )
        AND (
          (messages.history_id ~ '^[0-9]+$' AND messages.history_id::NUMERIC <= CAST(:final_history_id AS NUMERIC))
          OR (
            (messages.history_id IS NULL OR messages.history_id !~ '^[0-9]+$')
            AND messages.updated_at <= :reconcile_started_at
          )
        )
    """
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        state = connection.execute(
            text(
                """
                SELECT reconcile_started_at
                FROM gmail_import_state
                WHERE user_id = :user_id
                  AND reconcile_generation = :generation_id
                  AND reconcile_cursor IS NULL
                FOR UPDATE
                """
            ),
            {"user_id": user_id, "generation_id": generation_id},
        ).mappings().first()
        if state is None or state["reconcile_started_at"] is None:
            return None
        params = {
            "user_id": user_id,
            "generation_id": generation_id,
            "final_history_id": final_history_id,
            "reconcile_started_at": state["reconcile_started_at"],
        }
        affected_rows = connection.execute(
            text(
                f"""
                SELECT DISTINCT members.group_id
                FROM mail_group_members AS members
                JOIN gmail_messages AS messages
                  ON messages.user_id = members.user_id
                 AND messages.message_id = members.gmail_message_id
                WHERE members.user_id = :user_id
                  AND {missing_predicate}
                """
            ),
            params,
        ).mappings().all()
        connection.execute(
            text(
                f"""
                DELETE FROM mail_group_members AS members
                USING gmail_messages AS messages
                WHERE members.user_id = :user_id
                  AND messages.user_id = members.user_id
                  AND messages.message_id = members.gmail_message_id
                  AND {missing_predicate}
                """
            ),
            params,
        )
        deleted = connection.execute(
            text(
                f"""
                DELETE FROM gmail_messages AS messages
                WHERE {missing_predicate}
                """
            ),
            params,
        )
        completed_state = connection.execute(
            text(
                """
                UPDATE gmail_import_state
                SET last_history_id = CASE
                      WHEN history_cursor_authoritative IS NOT TRUE
                        THEN CAST(:final_history_id AS TEXT)
                      WHEN last_history_id IS NULL OR last_history_id !~ '^[0-9]+$'
                        THEN CAST(:final_history_id AS TEXT)
                      WHEN CAST(:final_history_id AS NUMERIC) >= last_history_id::NUMERIC
                        THEN CAST(:final_history_id AS TEXT)
                      ELSE last_history_id
                    END,
                    reconcile_generation = NULL,
                    reconcile_cursor = NULL,
                    reconcile_baseline_history_id = NULL,
                    reconcile_started_at = NULL,
                    history_metadata_count = GREATEST(
                      history_metadata_count,
                      (
                        SELECT COUNT(DISTINCT COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id))::INTEGER
                        FROM gmail_messages AS messages
                        WHERE messages.user_id = :user_id
                      )
                    ),
                    history_body_ready_count = GREATEST(
                      history_body_ready_count,
                      (
                        SELECT COUNT(*)::INTEGER
                        FROM (
                          SELECT COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) AS thread_key
                          FROM gmail_messages AS messages
                          WHERE messages.user_id = :user_id
                          GROUP BY thread_key
                          HAVING BOOL_AND(COALESCE(messages.body_fetch_status, 'missing') IN ('fetched', 'unavailable'))
                        ) AS ready_threads
                      )
                    ),
                    estimated_total_count = GREATEST(
                      estimated_total_count,
                      (
                        SELECT COUNT(DISTINCT COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id))::INTEGER
                        FROM gmail_messages AS messages
                        WHERE messages.user_id = :user_id
                      )
                    ),
                    history_metadata_complete = TRUE,
                    history_body_complete = NOT EXISTS (
                      SELECT 1
                      FROM gmail_messages AS messages
                      WHERE messages.user_id = :user_id
                        AND COALESCE(messages.body_fetch_status, 'missing') NOT IN ('fetched', 'unavailable')
                    ),
                    phase = CASE
                      WHEN NOT EXISTS (
                        SELECT 1
                        FROM gmail_messages AS messages
                        WHERE messages.user_id = :user_id
                          AND COALESCE(messages.body_fetch_status, 'missing') NOT IN ('fetched', 'unavailable')
                      ) THEN 'complete'
                      WHEN initial_window_complete
                        AND initial_body_ready_count >= LEAST(25, initial_body_target_count)
                        THEN 'syncing_history'
                      ELSE phase
                    END,
                    full_backfill_started_at = COALESCE(full_backfill_started_at, reconcile_started_at),
                    full_backfill_completed_at = COALESCE(full_backfill_completed_at, now()),
                    last_import_completed_at = now(),
                    last_delta_sync_at = now(),
                    history_cursor_authoritative = TRUE,
                    last_sync_error = NULL,
                    last_progress_at = now(),
                    updated_at = now()
                WHERE user_id = :user_id
                  AND reconcile_generation = :generation_id
                RETURNING *
                """
            ),
            {
                "user_id": user_id,
                "generation_id": generation_id,
                "final_history_id": final_history_id,
            },
        ).mappings().first()
        connection.execute(
            text(
                """
                DELETE FROM gmail_reconcile_seen
                WHERE user_id = :user_id
                  AND generation_id = :generation_id
                """
            ),
            {"user_id": user_id, "generation_id": generation_id},
        )
    return GmailReconcileFinalizeResult(
        deleted_message_count=max(0, int(deleted.rowcount or 0)),
        affected_group_ids=[str(row["group_id"]) for row in affected_rows],
        progress=(
            gmail_sync_progress(_state_from_row(completed_state))
            if completed_state is not None and completed_state.get("sync_generation") is not None
            else None
        ),
    )


def reset_gmail_reconciliation(
    database_url: str,
    *,
    user_id: str,
    generation_id: str,
) -> bool:
    """Discard an unusable generation without touching canonical mail rows."""
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        reset = connection.execute(
            text(
                """
                UPDATE gmail_import_state
                SET reconcile_generation = NULL,
                    reconcile_cursor = NULL,
                    reconcile_baseline_history_id = NULL,
                    reconcile_started_at = NULL,
                    history_cursor_authoritative = FALSE,
                    updated_at = now()
                WHERE user_id = :user_id
                  AND reconcile_generation = :generation_id
                RETURNING user_id
                """
            ),
            {"user_id": user_id, "generation_id": generation_id},
        ).scalar_one_or_none()
        connection.execute(
            text(
                """
                DELETE FROM gmail_reconcile_seen
                WHERE user_id = :user_id
                  AND generation_id = :generation_id
                """
            ),
            {"user_id": user_id, "generation_id": generation_id},
        )
    return reset is not None


def existing_gmail_message_ids(database_url: str, *, user_id: str, message_ids: list[str]) -> set[str]:
    unique_message_ids = list(dict.fromkeys([message_id for message_id in message_ids if message_id]))
    if not unique_message_ids:
        return set()
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT message_id
                FROM gmail_messages
                WHERE user_id = :user_id
                  AND message_id = ANY(:message_ids)
                """
            ),
            {"user_id": user_id, "message_ids": unique_message_ids},
        ).mappings().all()
    return {str(row["message_id"]) for row in rows}


def replace_gmail_thread_orders(
    database_url: str,
    *,
    user_id: str,
    ordered_thread_ids_by_label: dict[str, list[str]],
) -> dict[str, str]:
    """Atomically publish complete Gmail thread-list generations for a user."""
    normalized: dict[str, list[str]] = {}
    for raw_label, raw_thread_ids in ordered_thread_ids_by_label.items():
        label = _normalized_mailbox_label(raw_label)
        if label != raw_label.strip().lower():
            raise ValueError(f"Unsupported mailbox label: {raw_label}")
        normalized[label] = list(dict.fromkeys(str(thread_id) for thread_id in raw_thread_ids if thread_id))
    if not normalized:
        return {}

    generation_ids = {label: str(uuid4()) for label in normalized}
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        for label in sorted(normalized):
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"gmail-thread-order:{user_id}:{label}"},
            )
            previous_active = connection.execute(
                text(
                    """
                    SELECT active_generation_id
                    FROM gmail_thread_order_state
                    WHERE user_id = :user_id AND label = :label
                    """
                ),
                {"user_id": user_id, "label": label},
            ).scalar_one_or_none()
            generation_id = generation_ids[label]
            entries = [
                {
                    "user_id": user_id,
                    "label": label,
                    "generation_id": generation_id,
                    "gmail_thread_id": thread_id,
                    "position": position,
                }
                for position, thread_id in enumerate(normalized[label])
            ]
            if entries:
                connection.execute(
                    text(
                        """
                        INSERT INTO gmail_thread_order_entries (
                          user_id, label, generation_id, gmail_thread_id, position, created_at
                        ) VALUES (
                          :user_id, :label, :generation_id, :gmail_thread_id, :position, now()
                        )
                        """
                    ),
                    entries,
                )
            connection.execute(
                text(
                    """
                    INSERT INTO gmail_thread_order_state (
                      user_id, label, active_generation_id, previous_generation_id, refreshed_at
                    ) VALUES (
                      :user_id, :label, :active_generation_id, :previous_generation_id, now()
                    )
                    ON CONFLICT (user_id, label) DO UPDATE SET
                      active_generation_id = excluded.active_generation_id,
                      previous_generation_id = gmail_thread_order_state.active_generation_id,
                      refreshed_at = now()
                    """
                ),
                {
                    "user_id": user_id,
                    "label": label,
                    "active_generation_id": generation_id,
                    "previous_generation_id": str(previous_active) if previous_active else None,
                },
            )
            keep_generations = [generation_id, *([str(previous_active)] if previous_active else [])]
            connection.execute(
                text(
                    """
                    DELETE FROM gmail_thread_order_entries
                    WHERE user_id = :user_id
                      AND label = :label
                      AND NOT (generation_id = ANY(:keep_generations))
                    """
                ),
                {"user_id": user_id, "label": label, "keep_generations": keep_generations},
            )
    return generation_ids


def _resolve_gmail_thread_order_generation(
    database_url: str,
    *,
    user_id: str,
    label: str,
    cursor_generation_id: str | None = None,
) -> str | None:
    """Resolve a complete active order or a retained generation from a cursor."""
    mailbox_label = _normalized_mailbox_label(label)
    label_clause = _mailbox_label_clause("messages", mailbox_label)
    with get_engine(database_url).connect() as connection:
        state = connection.execute(
            text(
                """
                SELECT active_generation_id, previous_generation_id
                FROM gmail_thread_order_state
                WHERE user_id = :user_id AND label = :label
                """
            ),
            {"user_id": user_id, "label": mailbox_label},
        ).mappings().first()
        if state is None:
            if cursor_generation_id:
                raise MailboxCursorError("Mailbox cursor expired; reload this mailbox")
            return None
        active_generation_id = str(state["active_generation_id"])
        previous_generation_id = str(state["previous_generation_id"]) if state["previous_generation_id"] else None
        if cursor_generation_id:
            if cursor_generation_id not in {active_generation_id, previous_generation_id}:
                raise MailboxCursorError("Mailbox cursor expired; reload this mailbox")
            return cursor_generation_id
        missing_rank = connection.execute(
            text(
                f"""
                SELECT EXISTS (
                  SELECT 1
                  FROM (
                    SELECT COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) AS thread_key
                    FROM gmail_messages AS messages
                    WHERE messages.user_id = :user_id
                      AND {label_clause}
                    GROUP BY thread_key
                  ) AS eligible_threads
                  LEFT JOIN gmail_thread_order_entries AS thread_order
                    ON thread_order.user_id = :user_id
                   AND thread_order.label = :label
                   AND thread_order.generation_id = :generation_id
                   AND thread_order.gmail_thread_id = eligible_threads.thread_key
                  WHERE thread_order.gmail_thread_id IS NULL
                )
                """
            ),
            {
                "user_id": user_id,
                "label": mailbox_label,
                "generation_id": active_generation_id,
            },
        ).scalar_one()
    return None if bool(missing_rank) else active_generation_id


def upsert_gmail_messages(database_url: str, messages: Iterable[GmailMessageRecord]) -> int:
    rows = list(messages)
    if not rows:
        return 0
    user_ids = {message.user_id for message in rows}
    if len(user_ids) != 1:
        raise ValueError("A Gmail message write must contain exactly one user")
    user_id = next(iter(user_ids))
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        _upsert_gmail_messages_on_connection(connection, rows)
    return len(rows)


def _upsert_gmail_messages_on_connection(connection, rows: list[GmailMessageRecord]) -> None:
    for message in rows:
        connection.execute(
            text(
                """
                    INSERT INTO gmail_messages (
                      user_id, message_id, gmail_thread_id, history_id, label_ids_json, internal_date,
                      subject, sender, recipients_json, headers_json, snippet, raw_payload_json,
                      html_body_sanitized, html_render_document, text_body, extracted_signals_json, body_hash,
                      body_fetch_status, body_fetched_at, body_fetch_error, render_doc_bytes,
                      ai_title, ai_title_generated_at, content_revision, attachment_descriptors_json,
                      attachment_descriptors_ready,
                      created_at, updated_at
                    ) VALUES (
                      :user_id, :message_id, :gmail_thread_id, :history_id, :label_ids_json, :internal_date,
                      :subject, :sender, :recipients_json, :headers_json, :snippet, :raw_payload_json,
                      :html_body_sanitized, :html_render_document, :text_body, :extracted_signals_json, :body_hash,
                      :body_fetch_status, :body_fetched_at, :body_fetch_error, :render_doc_bytes,
                      :ai_title, :ai_title_generated_at, :content_revision, :attachment_descriptors_json,
                      :attachment_descriptors_ready,
                      now(), now()
                    )
                    ON CONFLICT (user_id, message_id) DO UPDATE SET
                      gmail_thread_id = excluded.gmail_thread_id,
                      history_id = CASE
                        WHEN excluded.history_id IS NULL
                          OR excluded.history_id !~ '^[0-9]+$'
                          THEN gmail_messages.history_id
                        WHEN gmail_messages.history_id IS NULL
                          OR gmail_messages.history_id !~ '^[0-9]+$'
                          THEN excluded.history_id
                        WHEN excluded.history_id::NUMERIC > gmail_messages.history_id::NUMERIC
                          THEN excluded.history_id
                        ELSE gmail_messages.history_id
                      END,
                      label_ids_json = CASE
                        WHEN excluded.history_id IS NULL
                          OR excluded.history_id !~ '^[0-9]+$'
                          THEN gmail_messages.label_ids_json
                        WHEN gmail_messages.history_id IS NULL
                          OR gmail_messages.history_id !~ '^[0-9]+$'
                          THEN excluded.label_ids_json
                        WHEN excluded.history_id::NUMERIC > gmail_messages.history_id::NUMERIC
                          THEN excluded.label_ids_json
                        ELSE gmail_messages.label_ids_json
                      END,
                      internal_date = excluded.internal_date,
                      subject = excluded.subject,
                      sender = excluded.sender,
                      recipients_json = excluded.recipients_json,
                      headers_json = excluded.headers_json,
                      snippet = excluded.snippet,
                      raw_payload_json = CASE
                        WHEN excluded.body_fetch_status = 'fetched'
                          OR excluded.html_render_document IS NOT NULL
                          OR excluded.html_body_sanitized IS NOT NULL
                          OR excluded.text_body IS NOT NULL
                        THEN excluded.raw_payload_json
                        ELSE gmail_messages.raw_payload_json
                      END,
                      html_body_sanitized = COALESCE(excluded.html_body_sanitized, gmail_messages.html_body_sanitized),
                      html_render_document = COALESCE(excluded.html_render_document, gmail_messages.html_render_document),
                      text_body = COALESCE(excluded.text_body, gmail_messages.text_body),
                      attachment_descriptors_json = CASE
                        WHEN excluded.attachment_descriptors_ready
                          THEN excluded.attachment_descriptors_json
                        ELSE gmail_messages.attachment_descriptors_json
                      END,
                      attachment_descriptors_ready = (
                        gmail_messages.attachment_descriptors_ready
                        OR excluded.attachment_descriptors_ready
                      ),
                      extracted_signals_json = excluded.extracted_signals_json,
                      body_hash = excluded.body_hash,
                      body_fetch_status = CASE
                        WHEN excluded.body_fetch_status = 'fetched' THEN 'fetched'
                        WHEN gmail_messages.body_fetch_status IN ('fetched', 'unavailable')
                          THEN gmail_messages.body_fetch_status
                        ELSE excluded.body_fetch_status
                      END,
                      body_fetched_at = CASE
                        WHEN excluded.body_fetch_status = 'fetched' THEN COALESCE(excluded.body_fetched_at, now())
                        ELSE gmail_messages.body_fetched_at
                      END,
                      body_fetch_error = CASE
                        WHEN excluded.body_fetch_status = 'fetched' THEN NULL
                        WHEN excluded.body_fetch_status = 'failed' THEN excluded.body_fetch_error
                        ELSE gmail_messages.body_fetch_error
                      END,
                      render_doc_bytes = GREATEST(gmail_messages.render_doc_bytes, excluded.render_doc_bytes),
                      ai_title = COALESCE(gmail_messages.ai_title, excluded.ai_title),
                      ai_title_generated_at = COALESCE(gmail_messages.ai_title_generated_at, excluded.ai_title_generated_at),
                      content_revision = CASE
                        WHEN excluded.gmail_thread_id IS DISTINCT FROM gmail_messages.gmail_thread_id
                          OR excluded.internal_date IS DISTINCT FROM gmail_messages.internal_date
                          OR excluded.subject IS DISTINCT FROM gmail_messages.subject
                          OR excluded.sender IS DISTINCT FROM gmail_messages.sender
                          OR excluded.recipients_json IS DISTINCT FROM gmail_messages.recipients_json
                          OR excluded.headers_json IS DISTINCT FROM gmail_messages.headers_json
                          OR excluded.snippet IS DISTINCT FROM gmail_messages.snippet
                          OR excluded.body_hash IS DISTINCT FROM gmail_messages.body_hash
                          OR (
                            CASE
                              WHEN excluded.history_id IS NULL
                                OR excluded.history_id !~ '^[0-9]+$'
                                THEN gmail_messages.history_id
                              WHEN gmail_messages.history_id IS NULL
                                OR gmail_messages.history_id !~ '^[0-9]+$'
                                THEN excluded.history_id
                              WHEN excluded.history_id::NUMERIC > gmail_messages.history_id::NUMERIC
                                THEN excluded.history_id
                              ELSE gmail_messages.history_id
                            END
                          ) IS DISTINCT FROM gmail_messages.history_id
                          OR (
                            CASE
                              WHEN excluded.history_id IS NULL
                                OR excluded.history_id !~ '^[0-9]+$'
                                THEN gmail_messages.label_ids_json
                              WHEN gmail_messages.history_id IS NULL
                                OR gmail_messages.history_id !~ '^[0-9]+$'
                                THEN excluded.label_ids_json
                              WHEN excluded.history_id::NUMERIC > gmail_messages.history_id::NUMERIC
                                THEN excluded.label_ids_json
                              ELSE gmail_messages.label_ids_json
                            END
                          ) IS DISTINCT FROM gmail_messages.label_ids_json
                          OR (
                            CASE
                              WHEN excluded.body_fetch_status = 'fetched'
                                OR excluded.html_render_document IS NOT NULL
                                OR excluded.html_body_sanitized IS NOT NULL
                                OR excluded.text_body IS NOT NULL
                                THEN excluded.raw_payload_json
                              ELSE gmail_messages.raw_payload_json
                            END
                          ) IS DISTINCT FROM gmail_messages.raw_payload_json
                          OR COALESCE(excluded.html_render_document, gmail_messages.html_render_document)
                            IS DISTINCT FROM gmail_messages.html_render_document
                          OR COALESCE(excluded.html_body_sanitized, gmail_messages.html_body_sanitized)
                            IS DISTINCT FROM gmail_messages.html_body_sanitized
                          OR COALESCE(excluded.text_body, gmail_messages.text_body)
                            IS DISTINCT FROM gmail_messages.text_body
                          OR (
                            CASE
                              WHEN excluded.attachment_descriptors_ready
                                THEN excluded.attachment_descriptors_json
                              ELSE gmail_messages.attachment_descriptors_json
                            END
                          ) IS DISTINCT FROM gmail_messages.attachment_descriptors_json
                          OR (
                            CASE
                              WHEN excluded.body_fetch_status = 'fetched' THEN 'fetched'
                              WHEN gmail_messages.body_fetch_status IN ('fetched', 'unavailable')
                                THEN gmail_messages.body_fetch_status
                              ELSE excluded.body_fetch_status
                            END
                          ) IS DISTINCT FROM gmail_messages.body_fetch_status
                          OR (
                          excluded.body_fetch_status = 'fetched'
                          AND gmail_messages.body_fetch_status IS DISTINCT FROM 'fetched'
                        ) OR (
                          excluded.html_render_document IS NOT NULL
                          AND excluded.html_render_document IS DISTINCT FROM gmail_messages.html_render_document
                        ) OR (
                          excluded.html_body_sanitized IS NOT NULL
                          AND excluded.html_body_sanitized IS DISTINCT FROM gmail_messages.html_body_sanitized
                        ) OR (
                          excluded.text_body IS NOT NULL
                          AND excluded.text_body IS DISTINCT FROM gmail_messages.text_body
                        )
                        THEN gmail_messages.content_revision + 1
                        ELSE gmail_messages.content_revision
                      END,
                      updated_at = now()
                """
            ),
            _message_params(message),
        )
    if rows:
        connection.execute(
            text(
                """
                UPDATE gmail_import_state
                SET history_body_complete = FALSE,
                    phase = CASE WHEN phase = 'complete' THEN 'syncing_recent' ELSE phase END,
                    updated_at = now()
                WHERE user_id = :user_id
                  AND sync_generation IS NOT NULL
                  AND history_metadata_complete
                  AND EXISTS (
                    SELECT 1
                    FROM gmail_messages
                    WHERE user_id = :user_id
                      AND COALESCE(body_fetch_status, 'missing') NOT IN ('fetched', 'unavailable')
                  )
                """
            ),
            {"user_id": rows[0].user_id},
        )


def force_update_gmail_message_labels(
    database_url: str,
    *,
    user_id: str,
    labels_by_message_id: dict[str, list[str]],
) -> int:
    """Apply local optimistic or rollback labels without changing provider history."""
    rows = [
        {
            "user_id": user_id,
            "message_id": message_id,
            "label_ids_json": json.dumps(list(dict.fromkeys(labels)), ensure_ascii=True),
        }
        for message_id, labels in labels_by_message_id.items()
        if message_id
    ]
    if not rows:
        return 0
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        for row in rows:
            connection.execute(
                text(
                    """
                    UPDATE gmail_messages
                    SET label_ids_json = :label_ids_json,
                        content_revision = CASE
                          WHEN label_ids_json IS DISTINCT FROM :label_ids_json
                            THEN content_revision + 1
                          ELSE content_revision
                        END,
                        updated_at = now()
                    WHERE user_id = :user_id
                      AND message_id = :message_id
                    """
                ),
                row,
            )
    return len(rows)


def update_gmail_message_bodies(database_url: str, messages: Iterable[GmailMessageRecord]) -> list[str]:
    """Persist full body data without allowing a stale body GET to overwrite mailbox metadata."""
    rows = list(messages)
    if not rows:
        return GmailBodyUpdateResult([], None)
    user_ids = {message.user_id for message in rows}
    if len(user_ids) != 1:
        raise ValueError("A Gmail body write must contain exactly one user")
    params = [_message_params(message) for message in rows]
    if any(row["body_fetch_status"] != "fetched" for row in params):
        raise ValueError("A Gmail body write must contain only terminal fetched messages")
    updated_message_ids: list[str] = []
    user_id = next(iter(user_ids))
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        for row in params:
            result = connection.execute(
                text(
                    """
                    UPDATE gmail_messages
                    SET raw_payload_json = :raw_payload_json,
                        html_body_sanitized = :html_body_sanitized,
                        html_render_document = :html_render_document,
                        text_body = :text_body,
                        attachment_descriptors_json = :attachment_descriptors_json,
                        attachment_descriptors_ready = TRUE,
                        extracted_signals_json = :extracted_signals_json,
                        body_hash = :body_hash,
                        body_fetch_status = 'fetched',
                        body_fetched_at = now(),
                        body_fetch_error = NULL,
                        render_doc_bytes = GREATEST(render_doc_bytes, :render_doc_bytes),
                        content_revision = content_revision + 1
                    WHERE user_id = :user_id
                      AND message_id = :message_id
                      AND body_fetch_status IS DISTINCT FROM 'fetched'
                    RETURNING message_id
                    """
                ),
                row,
            )
            updated_message_id = result.scalar_one_or_none()
            if updated_message_id is not None:
                updated_message_ids.append(str(updated_message_id))
        affected_thread_ids = list(
            dict.fromkeys(
                message.gmail_thread_id or message.message_id
                for message in rows
                if message.gmail_thread_id or message.message_id
            )
        )
        if affected_thread_ids:
            connection.execute(
                text(
                    """
                    UPDATE gmail_initial_window_entries AS entries
                    SET body_ready_at = COALESCE(entries.body_ready_at, now()),
                        updated_at = now()
                    WHERE entries.user_id = :user_id
                      AND entries.gmail_thread_id = ANY(:thread_ids)
                      AND entries.generation_id = (
                        SELECT state.sync_generation
                        FROM gmail_import_state AS state
                        WHERE state.user_id = :user_id
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM gmail_messages AS messages
                        WHERE messages.user_id = entries.user_id
                          AND COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) = entries.gmail_thread_id
                          AND COALESCE(messages.body_fetch_status, 'missing') NOT IN ('fetched', 'unavailable')
                      )
                    """
                ),
                {"user_id": user_id, "thread_ids": affected_thread_ids},
            )
        _update_gmail_body_progress_on_connection(connection, user_id=user_id)
        state_row = connection.execute(
            text("SELECT * FROM gmail_import_state WHERE user_id = :user_id"),
            {"user_id": user_id},
        ).mappings().first()
        progress = (
            gmail_sync_progress(_state_from_row(state_row))
            if state_row is not None and state_row.get("sync_generation") is not None
            else None
        )
    return GmailBodyUpdateResult(updated_message_ids, progress)


def _update_gmail_body_progress_on_connection(connection, *, user_id: str) -> None:
    counts = connection.execute(
        text(
            """
            SELECT
              (
                SELECT COUNT(*)::INTEGER
                FROM gmail_initial_window_entries AS entries
                JOIN gmail_import_state AS state
                  ON state.user_id = entries.user_id
                 AND state.sync_generation = entries.generation_id
                WHERE entries.user_id = :user_id
                  AND entries.body_ready_at IS NOT NULL
                  AND entries.position < state.initial_body_target_count
              ) AS initial_body_ready_count,
              (
                SELECT COUNT(*)::INTEGER
                FROM (
                  SELECT COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) AS thread_key
                  FROM gmail_messages AS messages
                  WHERE messages.user_id = :user_id
                  GROUP BY thread_key
                  HAVING BOOL_AND(COALESCE(messages.body_fetch_status, 'missing') IN ('fetched', 'unavailable'))
                ) AS ready_threads
              ) AS ready_thread_count
            """
        ),
        {"user_id": user_id},
    ).mappings().one()
    initial_ready = int(counts["initial_body_ready_count"] or 0)
    ready_threads = int(counts["ready_thread_count"] or 0)
    connection.execute(
        text(
            """
            UPDATE gmail_import_state
            SET initial_body_ready_count = GREATEST(initial_body_ready_count, :initial_ready),
                history_body_ready_count = GREATEST(
                  history_body_ready_count,
                  LEAST(history_metadata_count, :ready_threads)
                ),
                phase = CASE
                  WHEN history_metadata_complete
                    AND NOT EXISTS (
                      SELECT 1 FROM gmail_messages
                      WHERE user_id = :user_id
                        AND COALESCE(body_fetch_status, 'missing') NOT IN ('fetched', 'unavailable')
                    )
                    THEN 'complete'
                  WHEN initial_window_complete
                    AND :initial_ready >= initial_body_target_count
                    AND phase IN ('discovering_recent', 'importing_metadata', 'hydrating_priority_content')
                    THEN 'usable'
                  ELSE phase
                END,
                history_body_complete = history_body_complete OR (
                  history_metadata_complete
                  AND NOT EXISTS (
                    SELECT 1 FROM gmail_messages
                    WHERE user_id = :user_id
                      AND COALESCE(body_fetch_status, 'missing') NOT IN ('fetched', 'unavailable')
                  )
                ),
                last_progress_at = now(),
                updated_at = now()
            WHERE user_id = :user_id
              AND sync_generation IS NOT NULL
            """
        ),
        {
            "user_id": user_id,
            "initial_ready": initial_ready,
            "ready_threads": ready_threads,
        },
    )


def list_messages_needing_body_fetch(
    database_url: str,
    *,
    user_id: str,
    limit: int = 25,
) -> list[GmailMessageRecord]:
    """Return the next durable body-backfill transaction, newest first."""
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT *
                FROM gmail_messages
                WHERE user_id = :user_id
                  AND (
                    body_fetch_status = 'missing'
                    OR (
                      body_fetch_status = 'pending'
                      AND COALESCE(body_fetch_updated_at, updated_at) < now() - interval '15 minutes'
                    )
                  )
                ORDER BY internal_date DESC NULLS LAST, message_id DESC
                LIMIT :limit
                """
            ),
            {"user_id": user_id, "limit": max(1, min(int(limit), 25))},
        ).mappings().all()
    return [_message_from_row(row) for row in rows]


def backfill_gmail_attachment_descriptors_page(
    database_url: str,
    *,
    user_id: str,
    limit: int = 25,
    after_message_id: str | None = None,
) -> tuple[int, bool, str | None]:
    """Converge one fixed-size legacy MIME page to attachment descriptors.

    Migration 0031 deliberately performs no table scan or data rewrite. This
    resumable worker step reads at most 25 raw payloads and commits that page,
    so existing accounts converge without a release-time lock amplification.
    """

    bounded_limit = max(1, min(int(limit), 25))
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        rows = connection.execute(
            text(
                """
                SELECT message_id, raw_payload_json
                FROM gmail_messages
                WHERE user_id = :user_id
                  AND NOT attachment_descriptors_ready
                  AND (:after_message_id IS NULL OR message_id > :after_message_id)
                ORDER BY message_id ASC
                LIMIT :limit
                FOR UPDATE SKIP LOCKED
                """
            ),
            {
                "user_id": user_id,
                "limit": bounded_limit,
                "after_message_id": after_message_id,
            },
        ).mappings().all()
        processed = 0
        for row in rows:
            try:
                raw_payload = json.loads(row["raw_payload_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                raw_payload = {}
            descriptors = _gmail_attachment_descriptors(raw_payload)
            connection.execute(
                text(
                    """
                    UPDATE gmail_messages
                    SET attachment_descriptors_json = :attachment_descriptors_json,
                        attachment_descriptors_ready = TRUE,
                        content_revision = CASE
                          WHEN attachment_descriptors_json IS DISTINCT FROM :attachment_descriptors_json
                            THEN content_revision + 1
                          ELSE content_revision
                        END,
                        updated_at = now()
                    WHERE user_id = :user_id
                      AND message_id = :message_id
                      AND NOT attachment_descriptors_ready
                    """
                ),
                {
                    "user_id": user_id,
                    "message_id": str(row["message_id"]),
                    "attachment_descriptors_json": json.dumps(
                        descriptors,
                        ensure_ascii=True,
                    ),
                },
            )
            processed += 1
        page_cursor = str(rows[-1]["message_id"]) if rows else after_message_id
        next_message_id = connection.execute(
            text(
                """
                SELECT message_id
                FROM gmail_messages
                WHERE user_id = :user_id
                  AND NOT attachment_descriptors_ready
                  AND (:page_cursor IS NULL OR message_id > :page_cursor)
                ORDER BY message_id ASC
                LIMIT 1
                """
            ),
            {"user_id": user_id, "page_cursor": page_cursor},
        ).scalar_one_or_none()
        if next_message_id is None:
            connection.execute(
                text(
                    """
                    UPDATE gmail_import_state
                    SET attachment_descriptors_complete = TRUE,
                        updated_at = now()
                    WHERE user_id = :user_id
                    """
                ),
                {"user_id": user_id},
            )
    return processed, next_message_id is not None, page_cursor


def mark_gmail_history_body_complete_if_ready(
    database_url: str,
    *,
    user_id: str,
) -> GmailSyncProgress:
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        _update_gmail_body_progress_on_connection(connection, user_id=user_id)
        row = connection.execute(
            text("SELECT * FROM gmail_import_state WHERE user_id = :user_id"),
            {"user_id": user_id},
        ).mappings().first()
    return gmail_sync_progress(_state_from_row(row) if row is not None else None)


def update_gmail_message_ai_titles(database_url: str, *, user_id: str, titles: dict[str, str], generated_at: str) -> int:
    clean_titles = {
        str(message_id): str(title).strip()[:180]
        for message_id, title in titles.items()
        if str(message_id).strip() and str(title).strip()
    }
    if not clean_titles:
        return 0
    rows = [{"message_id": message_id, "ai_title": title} for message_id, title in clean_titles.items()]
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        result = connection.execute(
            text(
                """
                WITH input_titles AS (
                  SELECT *
                  FROM jsonb_to_recordset(CAST(:titles_json AS jsonb))
                    AS title_rows(message_id text, ai_title text)
                )
                UPDATE gmail_messages AS messages
                SET
                  ai_title = input_titles.ai_title,
                  ai_title_generated_at = CAST(:generated_at AS timestamptz),
                  updated_at = now()
                FROM input_titles
                WHERE messages.user_id = :user_id
                  AND messages.message_id = input_titles.message_id
                """
            ),
            {"user_id": user_id, "titles_json": json.dumps(rows, ensure_ascii=True), "generated_at": generated_at},
        )
    return int(result.rowcount or 0)


def mark_gmail_messages_body_fetch_state(
    database_url: str,
    *,
    user_id: str,
    message_ids: list[str],
    status: str,
    error: str | None = None,
) -> int:
    unique_message_ids = list(dict.fromkeys([message_id for message_id in message_ids if message_id]))
    if not unique_message_ids:
        return 0
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        result = connection.execute(
            text(
                """
                UPDATE gmail_messages
                SET
                  body_fetch_status = :status,
                  body_fetch_updated_at = now(),
                  body_fetched_at = CASE
                    WHEN :status IN ('fetched', 'unavailable') THEN now()
                    ELSE body_fetched_at
                  END,
                  body_fetch_error = :error,
                  content_revision = CASE
                    WHEN :status = 'unavailable'
                      AND COALESCE(body_fetch_status, 'missing') NOT IN ('fetched', 'unavailable')
                      THEN content_revision + 1
                    ELSE content_revision
                  END
                WHERE user_id = :user_id
                  AND message_id = ANY(:message_ids)
                  AND (
                    :status = 'fetched'
                    OR COALESCE(body_fetch_status, 'missing') NOT IN ('fetched', 'unavailable')
                  )
                """
            ),
            {
                "user_id": user_id,
                "message_ids": unique_message_ids,
                "status": status,
                "error": error[:4000] if error else None,
            },
        )
        updated_count = int(result.rowcount or 0)
        if updated_count > 0 and status in {"fetched", "unavailable"}:
            connection.execute(
                text(
                    """
                    UPDATE gmail_initial_window_entries AS entries
                    SET body_ready_at = COALESCE(entries.body_ready_at, now()),
                        updated_at = now()
                    WHERE entries.user_id = :user_id
                      AND entries.generation_id = (
                        SELECT state.sync_generation
                        FROM gmail_import_state AS state
                        WHERE state.user_id = :user_id
                      )
                      AND entries.gmail_thread_id IN (
                        SELECT DISTINCT COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id)
                        FROM gmail_messages AS messages
                        WHERE messages.user_id = :user_id
                          AND messages.message_id = ANY(:message_ids)
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM gmail_messages AS messages
                        WHERE messages.user_id = entries.user_id
                          AND COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) = entries.gmail_thread_id
                          AND COALESCE(messages.body_fetch_status, 'missing') NOT IN ('fetched', 'unavailable')
                      )
                    """
                ),
                {"user_id": user_id, "message_ids": unique_message_ids},
            )
            _update_gmail_body_progress_on_connection(connection, user_id=user_id)
    return updated_count


def upsert_pending_thread_action(
    database_url: str,
    *,
    user_id: str,
    client_action_id: str,
    mailbox_thread_id: str,
    target_message_id: str | None = None,
    action: str,
    created_at: str,
    previous_labels: dict[str, list[str]] | None = None,
) -> tuple[PendingThreadActionRecord, bool]:
    server_action_id = str(uuid4())
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        row = connection.execute(
            text(
                """
                INSERT INTO gmail_pending_thread_actions (
                  server_action_id, client_action_id, user_id, mailbox_thread_id, target_message_id, action,
                  state, created_at, queued_at, previous_labels_json, updated_at
                ) VALUES (
                  :server_action_id, :client_action_id, :user_id, :mailbox_thread_id, :target_message_id, :action,
                  'queued', :created_at, now(), CAST(:previous_labels_json AS JSONB), now()
                )
                ON CONFLICT (user_id, client_action_id) DO NOTHING
                RETURNING *
                """
            ),
            {
                "server_action_id": server_action_id,
                "client_action_id": client_action_id,
                "user_id": user_id,
                "mailbox_thread_id": mailbox_thread_id,
                "target_message_id": target_message_id,
                "action": action,
                "created_at": created_at,
                "previous_labels_json": json.dumps(previous_labels or {}, ensure_ascii=True),
            },
        ).mappings().first()
        created = row is not None
        if row is None:
            row = connection.execute(
                text(
                    """
                    SELECT *
                    FROM gmail_pending_thread_actions
                    WHERE user_id = :user_id
                      AND client_action_id = :client_action_id
                    """
                ),
                {"user_id": user_id, "client_action_id": client_action_id},
            ).mappings().first()
    if row is None:
        raise RuntimeError("Failed to enqueue Gmail thread action")
    return _pending_thread_action_from_row(row), created


def get_pending_thread_action(database_url: str, *, user_id: str, server_action_id: str) -> PendingThreadActionRecord | None:
    with get_engine(database_url).connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT *
                FROM gmail_pending_thread_actions
                WHERE user_id = :user_id
                  AND server_action_id = :server_action_id
                """
            ),
            {"user_id": user_id, "server_action_id": server_action_id},
        ).mappings().first()
    return _pending_thread_action_from_row(row) if row is not None else None


def count_pending_thread_actions(database_url: str, *, user_id: str) -> int:
    with get_engine(database_url).connect() as connection:
        value = connection.execute(
            text(
                """
                SELECT COUNT(*)
                FROM gmail_pending_thread_actions
                WHERE user_id = :user_id
                  AND state IN ('queued', 'applying')
                """
            ),
            {"user_id": user_id},
        ).scalar_one()
    return int(value)


def insert_mailbox_event(
    database_url: str,
    *,
    user_id: str,
    event_type: str,
    mailbox_label: str | None = None,
    payload: dict[str, Any] | None = None,
) -> MailboxEventRecord:
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        row = connection.execute(
            text(
                """
                INSERT INTO mailbox_events (
                  user_id, event_type, mailbox_label, payload_json, created_at
                ) VALUES (
                  :user_id, :event_type, :mailbox_label, CAST(:payload_json AS JSONB), now()
                )
                RETURNING *
                """
            ),
            {
                "user_id": user_id,
                "event_type": event_type,
                "mailbox_label": mailbox_label,
                "payload_json": json.dumps(payload or {}, ensure_ascii=True),
            },
        ).mappings().first()
    if row is None:
        raise RuntimeError("Failed to insert mailbox event")
    return _mailbox_event_from_row(row)


def list_mailbox_events_after(
    database_url: str,
    *,
    user_id: str,
    after_id: int | None = None,
    limit: int = 100,
) -> list[MailboxEventRecord]:
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT *
                FROM mailbox_events
                WHERE user_id = :user_id
                  AND id > :after_id
                ORDER BY id ASC
                LIMIT :limit
                """
            ),
            {"user_id": user_id, "after_id": int(after_id or 0), "limit": max(1, min(limit, 500))},
        ).mappings().all()
    return [_mailbox_event_from_row(row) for row in rows]


def latest_mailbox_event(
    database_url: str,
    *,
    user_id: str,
    event_type: str | None = None,
) -> MailboxEventRecord | None:
    clauses = ["user_id = :user_id"]
    params: dict[str, Any] = {"user_id": user_id}
    if event_type:
        clauses.append("event_type = :event_type")
        params["event_type"] = event_type
    with get_engine(database_url).connect() as connection:
        row = connection.execute(
            text(
                f"""
                SELECT *
                FROM mailbox_events
                WHERE {" AND ".join(clauses)}
                ORDER BY id DESC
                LIMIT 1
                """
            ),
            params,
        ).mappings().first()
    return _mailbox_event_from_row(row) if row is not None else None


def upsert_pending_send(
    database_url: str,
    *,
    user_id: str,
    client_send_id: str,
    send_type: str,
    mailbox_thread_id: str | None,
    gmail_thread_id: str | None,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body_text: str,
    body_html: str | None,
    headers: dict[str, Any],
    attachments: list[dict[str, str]],
    created_at: str,
) -> PendingSendRecord:
    server_send_id = str(uuid4())
    request_hash = _pending_send_request_hash(
        send_type=send_type,
        mailbox_thread_id=mailbox_thread_id,
        gmail_thread_id=gmail_thread_id,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        headers=headers,
        attachments=attachments,
        created_at=created_at,
    )
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        row = connection.execute(
            text(
                """
                INSERT INTO gmail_pending_sends (
                  server_send_id, client_send_id, user_id, send_type, mailbox_thread_id,
                  gmail_thread_id, to_json, cc_json, bcc_json, subject, body_text,
                  body_html, headers_json, attachments_json, request_hash,
                  state, created_at, queued_at, updated_at
                ) VALUES (
                  :server_send_id, :client_send_id, :user_id, :send_type, :mailbox_thread_id,
                  :gmail_thread_id, :to_json, :cc_json, :bcc_json, :subject, :body_text,
                  :body_html, :headers_json, CAST(:attachments_json AS JSONB), :request_hash,
                  'queued', :created_at, now(), now()
                )
                ON CONFLICT (user_id, client_send_id) DO UPDATE SET
                  request_hash = CASE
                    WHEN gmail_pending_sends.request_hash = '' THEN excluded.request_hash
                    ELSE gmail_pending_sends.request_hash
                  END,
                  updated_at = gmail_pending_sends.updated_at
                WHERE gmail_pending_sends.request_hash = excluded.request_hash
                   OR (
                     gmail_pending_sends.request_hash = ''
                     AND gmail_pending_sends.send_type = excluded.send_type
                     AND gmail_pending_sends.mailbox_thread_id IS NOT DISTINCT FROM excluded.mailbox_thread_id
                     AND gmail_pending_sends.gmail_thread_id IS NOT DISTINCT FROM excluded.gmail_thread_id
                     AND CAST(gmail_pending_sends.to_json AS JSONB) = CAST(excluded.to_json AS JSONB)
                     AND CAST(gmail_pending_sends.cc_json AS JSONB) = CAST(excluded.cc_json AS JSONB)
                     AND CAST(gmail_pending_sends.bcc_json AS JSONB) = CAST(excluded.bcc_json AS JSONB)
                     AND gmail_pending_sends.subject = excluded.subject
                     AND gmail_pending_sends.body_text = excluded.body_text
                     AND gmail_pending_sends.body_html IS NOT DISTINCT FROM excluded.body_html
                     AND CAST(gmail_pending_sends.headers_json AS JSONB) = CAST(excluded.headers_json AS JSONB)
                     AND gmail_pending_sends.attachments_json = excluded.attachments_json
                     AND gmail_pending_sends.created_at = excluded.created_at
                   )
                RETURNING *
                """
            ),
            {
                "server_send_id": server_send_id,
                "client_send_id": client_send_id,
                "user_id": user_id,
                "send_type": send_type,
                "mailbox_thread_id": mailbox_thread_id,
                "gmail_thread_id": gmail_thread_id,
                "to_json": json.dumps(to, ensure_ascii=True),
                "cc_json": json.dumps(cc, ensure_ascii=True),
                "bcc_json": json.dumps(bcc, ensure_ascii=True),
                "subject": subject,
                "body_text": body_text,
                "body_html": body_html,
                "headers_json": json.dumps(headers, ensure_ascii=True),
                "attachments_json": json.dumps(attachments, ensure_ascii=True),
                "request_hash": request_hash,
                "created_at": created_at,
            },
        ).mappings().first()
    if row is None:
        raise MailSendIdempotencyConflict("This send identity was already used for a different email.")
    return _pending_send_from_row(row)


def _pending_send_request_hash(
    *,
    send_type: str,
    mailbox_thread_id: str | None,
    gmail_thread_id: str | None,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body_text: str,
    body_html: str | None,
    headers: dict[str, Any],
    attachments: list[dict[str, str]],
    created_at: str,
) -> str:
    canonical = json.dumps(
        {
            "send_type": send_type,
            "mailbox_thread_id": mailbox_thread_id,
            "gmail_thread_id": gmail_thread_id,
            "to": to,
            "cc": cc,
            "bcc": bcc,
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
            "headers": headers,
            "attachments": attachments,
            "created_at": created_at,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def get_client_draft(
    database_url: str,
    *,
    user_id: str,
    client_draft_id: str | None = None,
    gmail_draft_id: str | None = None,
) -> ClientDraftRecord | None:
    if not client_draft_id and not gmail_draft_id:
        return None
    field_name = "client_draft_id" if client_draft_id else "gmail_draft_id"
    value = client_draft_id or gmail_draft_id
    with get_engine(database_url).connect() as connection:
        row = connection.execute(
            text(f"SELECT * FROM gmail_client_drafts WHERE user_id = :user_id AND {field_name} = :value"),
            {"user_id": user_id, "value": value},
        ).mappings().first()
    return _client_draft_from_row(row) if row is not None else None


def upsert_client_draft(
    database_url: str,
    *,
    user_id: str,
    client_draft_id: str,
    gmail_draft_id: str | None,
    gmail_message_id: str | None,
    gmail_thread_id: str | None,
    content_hash: str,
    state: str,
    created_at: str,
    error: str | None = None,
) -> ClientDraftRecord:
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        row = connection.execute(
            text(
                """
                INSERT INTO gmail_client_drafts (
                  user_id, client_draft_id, gmail_draft_id, gmail_message_id, gmail_thread_id,
                  content_hash, state, error, created_at, saved_at, updated_at
                ) VALUES (
                  :user_id, :client_draft_id, :gmail_draft_id, :gmail_message_id, :gmail_thread_id,
                  :content_hash, :state, :error, :created_at,
                  CASE WHEN :state = 'saved' THEN now() ELSE NULL END, now()
                )
                ON CONFLICT (user_id, client_draft_id) DO UPDATE SET
                  gmail_draft_id = COALESCE(EXCLUDED.gmail_draft_id, gmail_client_drafts.gmail_draft_id),
                  gmail_message_id = COALESCE(EXCLUDED.gmail_message_id, gmail_client_drafts.gmail_message_id),
                  gmail_thread_id = COALESCE(EXCLUDED.gmail_thread_id, gmail_client_drafts.gmail_thread_id),
                  content_hash = EXCLUDED.content_hash,
                  state = EXCLUDED.state,
                  error = EXCLUDED.error,
                  saved_at = CASE WHEN EXCLUDED.state = 'saved' THEN now() ELSE gmail_client_drafts.saved_at END,
                  updated_at = now()
                RETURNING *
                """
            ),
            {
                "user_id": user_id,
                "client_draft_id": client_draft_id,
                "gmail_draft_id": gmail_draft_id,
                "gmail_message_id": gmail_message_id,
                "gmail_thread_id": gmail_thread_id,
                "content_hash": content_hash,
                "state": state,
                "error": error,
                "created_at": created_at,
            },
        ).mappings().first()
    if row is None:
        raise RuntimeError("Failed to save Gmail draft identity")
    return _client_draft_from_row(row)


def mark_client_draft_sent(
    database_url: str,
    *,
    user_id: str,
    client_draft_id: str,
    client_send_id: str,
    gmail_message_id: str | None,
    gmail_thread_id: str | None,
) -> ClientDraftRecord | None:
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        row = connection.execute(
            text(
                """
                UPDATE gmail_client_drafts
                SET state = 'sent', last_client_send_id = :client_send_id,
                    sent_message_id = :gmail_message_id,
                    gmail_thread_id = COALESCE(:gmail_thread_id, gmail_thread_id),
                    error = NULL, updated_at = now()
                WHERE user_id = :user_id AND client_draft_id = :client_draft_id
                RETURNING *
                """
            ),
            {
                "user_id": user_id,
                "client_draft_id": client_draft_id,
                "client_send_id": client_send_id,
                "gmail_message_id": gmail_message_id,
                "gmail_thread_id": gmail_thread_id,
            },
        ).mappings().first()
    return _client_draft_from_row(row) if row is not None else None


def mark_client_draft_sending(
    database_url: str,
    *,
    user_id: str,
    client_draft_id: str,
    client_send_id: str,
) -> ClientDraftRecord | None:
    """Durably claim one saved draft send before Gmail is called."""
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        row = connection.execute(
            text(
                """
                UPDATE gmail_client_drafts
                SET state = 'sending', last_client_send_id = :client_send_id,
                    error = NULL, updated_at = now()
                WHERE user_id = :user_id
                  AND client_draft_id = :client_draft_id
                  AND state = 'saved'
                RETURNING *
                """
            ),
            {
                "user_id": user_id,
                "client_draft_id": client_draft_id,
                "client_send_id": client_send_id,
            },
        ).mappings().first()
    return _client_draft_from_row(row) if row is not None else None


def restore_client_draft_after_definite_send_failure(
    database_url: str,
    *,
    user_id: str,
    client_draft_id: str,
    client_send_id: str,
    error: str,
) -> ClientDraftRecord | None:
    """Make a definitely rejected send retryable without clearing its identity."""
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        row = connection.execute(
            text(
                """
                UPDATE gmail_client_drafts
                SET state = 'saved', error = :error, updated_at = now()
                WHERE user_id = :user_id
                  AND client_draft_id = :client_draft_id
                  AND state = 'sending'
                  AND last_client_send_id = :client_send_id
                RETURNING *
                """
            ),
            {
                "user_id": user_id,
                "client_draft_id": client_draft_id,
                "client_send_id": client_send_id,
                "error": error,
            },
        ).mappings().first()
    return _client_draft_from_row(row) if row is not None else None


def mark_client_draft_deleted(
    database_url: str,
    *,
    user_id: str,
    client_draft_id: str,
) -> ClientDraftRecord | None:
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        row = connection.execute(
            text(
                """
                UPDATE gmail_client_drafts
                SET state = 'deleted', error = NULL, updated_at = now()
                WHERE user_id = :user_id
                  AND client_draft_id = :client_draft_id
                  AND state NOT IN ('sending', 'sent')
                RETURNING *
                """
            ),
            {"user_id": user_id, "client_draft_id": client_draft_id},
        ).mappings().first()
    return _client_draft_from_row(row) if row is not None else None


def get_pending_send(database_url: str, *, user_id: str, server_send_id: str) -> PendingSendRecord | None:
    with get_engine(database_url).connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT *
                FROM gmail_pending_sends
                WHERE user_id = :user_id
                  AND server_send_id = :server_send_id
                """
            ),
            {"user_id": user_id, "server_send_id": server_send_id},
        ).mappings().first()
    return _pending_send_from_row(row) if row is not None else None


def list_outbox_sends(database_url: str, *, user_id: str, limit: int = 100) -> list[PendingSendRecord]:
    """Return unresolved durable sends without exposing another user's records."""
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT *
                FROM gmail_pending_sends
                WHERE user_id = :user_id
                  AND state IN ('queued', 'sending', 'failed')
                ORDER BY updated_at DESC, server_send_id DESC
                LIMIT :limit
                """
            ),
            {"user_id": user_id, "limit": max(1, min(limit, 200))},
        ).mappings().all()
    return [_pending_send_from_row(row) for row in rows]


def mark_pending_send_sending(database_url: str, *, user_id: str, server_send_id: str) -> PendingSendRecord | None:
    return _update_pending_send_state(database_url, user_id=user_id, server_send_id=server_send_id, state="sending")


def claim_pending_send(database_url: str, *, user_id: str, server_send_id: str) -> PendingSendRecord | None:
    """Atomically claim one queued or conclusively failed delivery."""
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        row = connection.execute(
            text(
                """
                UPDATE gmail_pending_sends
                SET state = 'sending', error = NULL, updated_at = now()
                WHERE user_id = :user_id
                  AND server_send_id = :server_send_id
                  AND state IN ('queued', 'failed')
                RETURNING *
                """
            ),
            {"user_id": user_id, "server_send_id": server_send_id},
        ).mappings().first()
    return _pending_send_from_row(row) if row is not None else None


def mark_pending_send_queued(
    database_url: str,
    *,
    user_id: str,
    server_send_id: str,
    error: str,
) -> PendingSendRecord | None:
    return _update_pending_send_state(
        database_url,
        user_id=user_id,
        server_send_id=server_send_id,
        state="queued",
        error=error[:4000],
    )


def mark_pending_send_sent(
    database_url: str,
    *,
    user_id: str,
    server_send_id: str,
    gmail_message_id: str | None,
    gmail_thread_id: str | None,
) -> PendingSendRecord | None:
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        row = connection.execute(
            text(
                """
                UPDATE gmail_pending_sends
                SET
                  state = 'sent',
                  gmail_message_id = :gmail_message_id,
                  gmail_thread_id = COALESCE(:gmail_thread_id, gmail_thread_id),
                  to_json = '[]',
                  cc_json = '[]',
                  bcc_json = '[]',
                  subject = '',
                  body_text = '',
                  body_html = NULL,
                  headers_json = '{}',
                  attachments_json = '[]'::jsonb,
                  sent_at = now(),
                  error = NULL,
                  updated_at = now()
                WHERE user_id = :user_id
                  AND server_send_id = :server_send_id
                RETURNING *
                """
            ),
            {
                "user_id": user_id,
                "server_send_id": server_send_id,
                "gmail_message_id": gmail_message_id,
                "gmail_thread_id": gmail_thread_id,
            },
        ).mappings().first()
    return _pending_send_from_row(row) if row is not None else None


def mark_pending_send_failed(database_url: str, *, user_id: str, server_send_id: str, error: str) -> PendingSendRecord | None:
    return _update_pending_send_state(
        database_url,
        user_id=user_id,
        server_send_id=server_send_id,
        state="failed",
        error=error[:4000],
    )


def _update_pending_send_state(
    database_url: str,
    *,
    user_id: str,
    server_send_id: str,
    state: str,
    error: str | None = None,
) -> PendingSendRecord | None:
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        row = connection.execute(
            text(
                """
                UPDATE gmail_pending_sends
                SET state = :state, error = :error, updated_at = now()
                WHERE user_id = :user_id
                  AND server_send_id = :server_send_id
                RETURNING *
                """
            ),
            {"user_id": user_id, "server_send_id": server_send_id, "state": state, "error": error},
        ).mappings().first()
    return _pending_send_from_row(row) if row is not None else None


def mark_pending_thread_action_applying(database_url: str, *, user_id: str, server_action_id: str) -> None:
    _update_pending_thread_action_state(database_url, user_id=user_id, server_action_id=server_action_id, state="applying")


def mark_pending_thread_action_applied(database_url: str, *, user_id: str, server_action_id: str) -> PendingThreadActionRecord | None:
    return _update_pending_thread_action_state(database_url, user_id=user_id, server_action_id=server_action_id, state="applied", applied=True)


def mark_pending_thread_action_failed(database_url: str, *, user_id: str, server_action_id: str, error: str) -> PendingThreadActionRecord | None:
    return _update_pending_thread_action_state(
        database_url,
        user_id=user_id,
        server_action_id=server_action_id,
        state="failed",
        error=error[:4000],
    )


def _update_pending_thread_action_state(
    database_url: str,
    *,
    user_id: str,
    server_action_id: str,
    state: str,
    applied: bool = False,
    error: str | None = None,
) -> PendingThreadActionRecord | None:
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        row = connection.execute(
            text(
                """
                UPDATE gmail_pending_thread_actions
                SET
                  state = :state,
                  applied_at = CASE WHEN :applied THEN now() ELSE applied_at END,
                  error = :error,
                  updated_at = now()
                WHERE user_id = :user_id
                  AND server_action_id = :server_action_id
                RETURNING *
                """
            ),
            {
                "user_id": user_id,
                "server_action_id": server_action_id,
                "state": state,
                "applied": applied,
                "error": error,
            },
        ).mappings().first()
    return _pending_thread_action_from_row(row) if row is not None else None


def delete_gmail_messages(database_url: str, *, user_id: str, message_ids: list[str]) -> list[str]:
    unique_message_ids = list(dict.fromkeys([message_id for message_id in message_ids if message_id]))
    if not unique_message_ids:
        return []
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        thread_rows = connection.execute(
            text(
                """
                SELECT DISTINCT COALESCE(NULLIF(gmail_thread_id, ''), message_id) AS thread_id
                FROM gmail_messages
                WHERE user_id = :user_id
                  AND message_id = ANY(:message_ids)
                """
            ),
            {"user_id": user_id, "message_ids": unique_message_ids},
        ).mappings().all()
        affected_thread_ids = [str(row["thread_id"]) for row in thread_rows]
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
        terminal_initial_entries = None
        resolved_initial_entries = None
        if affected_thread_ids:
            terminal_initial_entries = connection.execute(
                text(
                    """
                    UPDATE gmail_initial_window_entries AS entries
                    SET metadata_ready_at = COALESCE(entries.metadata_ready_at, now()),
                        body_ready_at = COALESCE(entries.body_ready_at, now()),
                        message_count = 0,
                        updated_at = now()
                    WHERE entries.user_id = :user_id
                      AND entries.generation_id = (
                        SELECT state.sync_generation
                        FROM gmail_import_state AS state
                        WHERE state.user_id = :user_id
                      )
                      AND entries.gmail_thread_id = ANY(:thread_ids)
                      AND NOT EXISTS (
                        SELECT 1
                        FROM gmail_messages AS messages
                        WHERE messages.user_id = entries.user_id
                          AND COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) = entries.gmail_thread_id
                      )
                    """
                ),
                {"user_id": user_id, "thread_ids": affected_thread_ids},
            )
            resolved_initial_entries = connection.execute(
                text(
                    """
                    UPDATE gmail_initial_window_entries AS entries
                    SET body_ready_at = COALESCE(entries.body_ready_at, now()),
                        updated_at = now()
                    WHERE entries.user_id = :user_id
                      AND entries.generation_id = (
                        SELECT state.sync_generation
                        FROM gmail_import_state AS state
                        WHERE state.user_id = :user_id
                      )
                      AND entries.gmail_thread_id = ANY(:thread_ids)
                      AND EXISTS (
                        SELECT 1
                        FROM gmail_messages AS messages
                        WHERE messages.user_id = entries.user_id
                          AND COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) = entries.gmail_thread_id
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM gmail_messages AS messages
                        WHERE messages.user_id = entries.user_id
                          AND COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) = entries.gmail_thread_id
                          AND COALESCE(messages.body_fetch_status, 'missing') NOT IN ('fetched', 'unavailable')
                      )
                    """
                ),
                {"user_id": user_id, "thread_ids": affected_thread_ids},
            )
        terminal_initial_count = int(getattr(terminal_initial_entries, "rowcount", 0) or 0)
        resolved_initial_count = int(getattr(resolved_initial_entries, "rowcount", 0) or 0)
        if terminal_initial_count > 0 or resolved_initial_count > 0:
            connection.execute(
                text(
                    """
                    WITH counts AS (
                      SELECT
                        COUNT(*)::INTEGER AS target_count,
                        COUNT(*) FILTER (WHERE entries.metadata_ready_at IS NOT NULL)::INTEGER AS metadata_count,
                        COUNT(*) FILTER (
                          WHERE entries.body_ready_at IS NOT NULL
                            AND entries.position < 25
                        )::INTEGER AS body_ready_count
                      FROM gmail_initial_window_entries AS entries
                      JOIN gmail_import_state AS active_state
                        ON active_state.user_id = entries.user_id
                       AND active_state.sync_generation = entries.generation_id
                      WHERE entries.user_id = :user_id
                    )
                    UPDATE gmail_import_state AS state
                    SET initial_target_count = GREATEST(state.initial_target_count, counts.target_count),
                        initial_metadata_count = GREATEST(state.initial_metadata_count, counts.metadata_count),
                        initial_body_target_count = GREATEST(state.initial_body_target_count, LEAST(25, counts.target_count)),
                        initial_body_ready_count = GREATEST(state.initial_body_ready_count, counts.body_ready_count),
                        initial_window_complete = state.initial_window_complete OR counts.metadata_count >= counts.target_count,
                        last_progress_at = now(),
                        updated_at = now()
                    FROM counts
                    WHERE state.user_id = :user_id
                      AND state.sync_generation IS NOT NULL
                    """
                ),
                {"user_id": user_id},
            )
            _update_gmail_body_progress_on_connection(connection, user_id=user_id)
    return [str(row["group_id"]) for row in rows]


def prune_empty_mail_groups(database_url: str, *, user_id: str, group_ids: list[str]) -> list[str]:
    unique_group_ids = list(dict.fromkeys([group_id for group_id in group_ids if group_id]))
    if not unique_group_ids:
        return []
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
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
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
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


def _gmail_message_stored_bytes_sql(table_alias: str) -> str:
    """Conservative stored-body byte expression for a trusted SQL alias."""

    columns = (
        "message_id",
        "gmail_thread_id",
        "history_id",
        "label_ids_json",
        "subject",
        "sender",
        "recipients_json",
        "headers_json",
        "snippet",
        "raw_payload_json",
        "html_body_sanitized",
        "html_render_document",
        "text_body",
        "extracted_signals_json",
        "body_hash",
        "body_fetch_error",
        "ai_title",
        "attachment_descriptors_json",
    )
    return " + ".join(
        f"octet_length(COALESCE({table_alias}.{column}, ''))"
        for column in columns
    )


def list_messages_for_gmail_thread(
    database_url: str,
    *,
    user_id: str,
    gmail_thread_id: str,
    maximum_messages: int | None = None,
    maximum_stored_bytes: int | None = None,
) -> list[GmailMessageRecord]:
    thread_id = gmail_thread_id.strip()
    if not thread_id:
        return []
    if maximum_messages is not None and maximum_messages < 1:
        return []
    if maximum_stored_bytes is not None and maximum_stored_bytes < 1:
        return []

    if maximum_messages is not None or maximum_stored_bytes is not None:
        # Keep oversized conversations inside Postgres instead of materializing
        # their complete body columns in the API process. The window aggregates
        # and eligibility predicate are evaluated in one statement, so a thread
        # that grows between the route preflight and this read still cannot
        # escape either bound.
        message_limit = maximum_messages or 2_147_483_647
        byte_limit = maximum_stored_bytes or 9_223_372_036_854_775_807
        with get_engine(database_url).connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT bounded.*
                    FROM (
                      SELECT
                        messages.*,
                        COUNT(*) OVER () AS snapshot_message_count,
                        SUM({_gmail_message_stored_bytes_sql('messages')}) OVER () AS snapshot_stored_bytes
                      FROM gmail_messages AS messages
                      WHERE messages.user_id = :user_id
                        AND COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) = :gmail_thread_id
                    ) AS bounded
                    WHERE bounded.snapshot_message_count <= :maximum_messages
                      AND bounded.snapshot_stored_bytes <= :maximum_stored_bytes
                    ORDER BY bounded.internal_date ASC NULLS LAST, bounded.created_at ASC
                    LIMIT :maximum_messages
                    """
                ),
                {
                    "user_id": user_id,
                    "gmail_thread_id": thread_id,
                    "maximum_messages": message_limit,
                    "maximum_stored_bytes": byte_limit,
                },
            ).mappings().all()
        return [_message_from_row(row) for row in rows]

    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT *
                FROM gmail_messages
                WHERE user_id = :user_id
                  AND COALESCE(NULLIF(gmail_thread_id, ''), message_id) = :gmail_thread_id
                ORDER BY internal_date ASC NULLS LAST, created_at ASC
                """
            ),
            {"user_id": user_id, "gmail_thread_id": thread_id},
        ).mappings().all()
    return [_message_from_row(row) for row in rows]


def get_gmail_thread_message_page(
    database_url: str,
    *,
    user_id: str,
    gmail_thread_id: str,
    limit: int,
    offset: int,
) -> GmailThreadMessagePage | None:
    """Return a DB-bounded reader page and stable whole-thread metadata.

    Only ``limit`` complete message rows cross the database boundary. Count,
    hydration state, latest subject, and the revision token are scalar
    aggregates produced by the same statement. This is intentionally separate
    from ``list_messages_for_gmail_thread`` because several mutation paths need
    a complete thread, while the public reader endpoint must remain bounded for
    conversations with thousands of messages.
    """

    thread_id = gmail_thread_id.strip()
    if not thread_id:
        return None
    if limit < 1:
        raise ValueError("Gmail thread page limit must be positive")
    if offset < 0:
        raise ValueError("Gmail thread page offset cannot be negative")

    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                WITH thread_stats AS (
                  SELECT
                    COUNT(*)::INTEGER AS thread_total_messages,
                    COUNT(*) FILTER (
                      WHERE COALESCE(messages.body_fetch_status, 'missing')
                        NOT IN ('fetched', 'unavailable')
                    )::INTEGER AS thread_incomplete_body_count,
                    SUBSTRING(
                      MD5(
                        COALESCE(
                          STRING_AGG(
                            CHAR_LENGTH(messages.message_id)::TEXT
                              || ':' || messages.message_id
                              || ':' || GREATEST(messages.content_revision, 1)::TEXT,
                            '' ORDER BY messages.message_id
                          ),
                          ''
                        )
                      ),
                      1,
                      24
                    ) AS thread_content_revision
                  FROM gmail_messages AS messages
                  WHERE messages.user_id = :user_id
                    AND COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) = :gmail_thread_id
                )
                SELECT
                  page_messages.*,
                  thread_stats.thread_total_messages,
                  thread_stats.thread_incomplete_body_count,
                  thread_stats.thread_content_revision,
                  latest_message.subject AS thread_latest_subject
                FROM thread_stats
                LEFT JOIN LATERAL (
                  SELECT messages.*
                  FROM gmail_messages AS messages
                  WHERE messages.user_id = :user_id
                    AND COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) = :gmail_thread_id
                  ORDER BY
                    messages.internal_date ASC NULLS LAST,
                    messages.created_at ASC,
                    messages.message_id ASC
                  LIMIT :page_limit
                  OFFSET :page_offset
                ) AS page_messages ON TRUE
                LEFT JOIN LATERAL (
                  SELECT messages.subject
                  FROM gmail_messages AS messages
                  WHERE messages.user_id = :user_id
                    AND COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) = :gmail_thread_id
                  ORDER BY
                    COALESCE(messages.internal_date, messages.updated_at) DESC,
                    messages.created_at ASC,
                    messages.message_id ASC
                  LIMIT 1
                ) AS latest_message ON TRUE
                """
            ),
            {
                "user_id": user_id,
                "gmail_thread_id": thread_id,
                "page_limit": limit,
                "page_offset": offset,
            },
        ).mappings().all()

    if not rows:
        return None
    total_messages = max(0, int(rows[0]["thread_total_messages"] or 0))
    if total_messages == 0:
        return None
    messages = [
        _message_from_row(row)
        for row in rows
        if row.get("message_id") is not None
    ]
    return GmailThreadMessagePage(
        gmail_thread_id=thread_id,
        messages=messages,
        total_messages=total_messages,
        latest_subject=(
            str(rows[0]["thread_latest_subject"])
            if rows[0].get("thread_latest_subject") is not None
            else None
        ),
        incomplete_body_count=max(0, int(rows[0]["thread_incomplete_body_count"] or 0)),
        content_revision=str(rows[0]["thread_content_revision"] or ""),
    )


def get_gmail_thread_snapshot_stats(
    database_url: str,
    *,
    user_id: str,
    gmail_thread_ids: list[str],
) -> dict[str, GmailThreadSnapshotStats]:
    """Return sizing/hydration facts without transferring complete bodies."""

    thread_ids = list(dict.fromkeys(thread_id.strip() for thread_id in gmail_thread_ids if thread_id.strip()))
    if not thread_ids:
        return {}
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                f"""
                SELECT
                  COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) AS gmail_thread_id,
                  COUNT(*)::INTEGER AS message_count,
                  COALESCE(SUM({_gmail_message_stored_bytes_sql('messages')}), 0)::BIGINT AS stored_bytes,
                  COUNT(*) FILTER (
                    WHERE COALESCE(messages.body_fetch_status, 'missing') NOT IN ('fetched', 'unavailable')
                  )::INTEGER AS incomplete_body_count
                FROM gmail_messages AS messages
                WHERE messages.user_id = :user_id
                  AND COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) = ANY(:gmail_thread_ids)
                GROUP BY COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id)
                """
            ),
            {"user_id": user_id, "gmail_thread_ids": thread_ids},
        ).mappings().all()
    return {
        str(row["gmail_thread_id"]): GmailThreadSnapshotStats(
            gmail_thread_id=str(row["gmail_thread_id"]),
            message_count=max(0, int(row["message_count"] or 0)),
            stored_bytes=max(0, int(row["stored_bytes"] or 0)),
            incomplete_body_count=max(0, int(row["incomplete_body_count"] or 0)),
        )
        for row in rows
    }


def list_mailbox_thread_messages(
    database_url: str,
    *,
    user_id: str,
    label: str,
    limit: int = 150,
    since_iso: str | None = None,
    search_query: str | None = None,
) -> list[tuple[str, list[GmailMessageRecord]]]:
    return list_mailbox_thread_page(
        database_url,
        user_id=user_id,
        label=label,
        limit=limit,
        since_iso=since_iso,
        search_query=search_query,
    ).threads


def list_mailbox_thread_page(
    database_url: str,
    *,
    user_id: str,
    label: str,
    limit: int = 150,
    cursor: str | None = None,
    since_iso: str | None = None,
    search_query: str | None = None,
) -> MailboxThreadPage:
    mailbox_label = _normalized_mailbox_label(label)
    label_clause = _mailbox_label_clause("messages", mailbox_label)
    normalized_query = (search_query or "").strip()
    cursor_state = _decode_mailbox_cursor_state(cursor) if cursor else None
    cursor_latest_at: str | None = None
    cursor_thread_key: str | None = None
    order_generation_id: str | None = None
    cursor_position: int | None = None
    if cursor_state is not None and cursor_state.mode == "gmail_order":
        if normalized_query or since_iso or cursor_state.label != mailbox_label:
            raise MailboxCursorError("Invalid mailbox cursor")
        if cursor_state.generation_id is None or cursor_state.position is None:
            raise MailboxCursorError("Invalid mailbox cursor")
        order_generation_id = _resolve_gmail_thread_order_generation(
            database_url,
            user_id=user_id,
            label=mailbox_label,
            cursor_generation_id=cursor_state.generation_id,
        )
        cursor_position = cursor_state.position
        cursor_thread_key = cursor_state.thread_key
    elif cursor_state is not None:
        cursor_latest_at = cursor_state.latest_at
        cursor_thread_key = cursor_state.thread_key
    elif not normalized_query and not since_iso:
        order_generation_id = _resolve_gmail_thread_order_generation(
            database_url,
            user_id=user_id,
            label=mailbox_label,
        )
    filters = [f"messages.user_id = :user_id", label_clause]
    params: dict[str, Any] = {"user_id": user_id, "limit": max(1, limit) + 1}
    if since_iso:
        filters.append("COALESCE(messages.internal_date, messages.updated_at) >= :since_iso")
        params["since_iso"] = since_iso
    if normalized_query:
        filters.append(
            "(POSITION(lower(:search_query) IN lower(COALESCE(messages.subject, ''))) > 0 "
            "OR POSITION(lower(:search_query) IN lower(COALESCE(messages.sender, ''))) > 0 "
            "OR POSITION(lower(:search_query) IN lower(COALESCE(messages.snippet, ''))) > 0 "
            "OR POSITION(lower(:search_query) IN lower(COALESCE(messages.text_body, ''))) > 0 "
            "OR POSITION(lower(:search_query) IN lower(COALESCE(messages.recipients_json::text, ''))) > 0)"
        )
        params["search_query"] = normalized_query[:200]
    if order_generation_id is not None:
        order_cursor_clause = ""
        if cursor_position is not None and cursor_thread_key:
            order_cursor_clause = """
                  AND (thread_order.position, matching_threads.thread_key)
                        > (:cursor_position, :cursor_thread_key)
            """
            params["cursor_position"] = cursor_position
            params["cursor_thread_key"] = cursor_thread_key
        params["mailbox_label"] = mailbox_label
        params["order_generation_id"] = order_generation_id
        page_threads_sql = f"""
                page_threads AS (
                  SELECT
                    matching_threads.thread_key,
                    matching_threads.latest_matching_at,
                    thread_order.position AS mailbox_order_position
                  FROM matching_threads
                  JOIN gmail_thread_order_entries AS thread_order
                    ON thread_order.user_id = :user_id
                   AND thread_order.label = :mailbox_label
                   AND thread_order.generation_id = :order_generation_id
                   AND thread_order.gmail_thread_id = matching_threads.thread_key
                  WHERE TRUE
                  {order_cursor_clause}
                  ORDER BY thread_order.position ASC, matching_threads.thread_key ASC
                  LIMIT :limit
                )
        """
        final_order_sql = """page_threads.mailbox_order_position ASC,
                         page_threads.thread_key ASC,"""
    else:
        date_cursor_clause = ""
        if cursor_latest_at and cursor_thread_key:
            date_cursor_clause = """
                  WHERE (matching_threads.latest_matching_at, matching_threads.thread_key)
                        < (CAST(:cursor_latest_at AS timestamptz), :cursor_thread_key)
            """
            params["cursor_latest_at"] = cursor_latest_at
            params["cursor_thread_key"] = cursor_thread_key
        page_threads_sql = f"""
                page_threads AS (
                  SELECT thread_key, latest_matching_at, CAST(NULL AS INTEGER) AS mailbox_order_position
                  FROM matching_threads
                  {date_cursor_clause}
                  ORDER BY latest_matching_at DESC, thread_key DESC
                  LIMIT :limit
                )
        """
        final_order_sql = """page_threads.latest_matching_at DESC,
                         page_threads.thread_key DESC,"""
    where_clause = "\n                    AND ".join(filters)
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                f"""
                WITH eligible_threads AS (
                  SELECT
                    COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) AS thread_key
                  FROM gmail_messages AS messages
                  WHERE {where_clause}
                  GROUP BY thread_key
                ),
                matching_threads AS (
                  SELECT
                    eligible_threads.thread_key,
                    MAX(COALESCE(all_messages.internal_date, all_messages.updated_at)) AS latest_matching_at
                  FROM eligible_threads
                  JOIN gmail_messages AS all_messages
                    ON all_messages.user_id = :user_id
                   AND COALESCE(NULLIF(all_messages.gmail_thread_id, ''), all_messages.message_id) = eligible_threads.thread_key
                  GROUP BY eligible_threads.thread_key
                ),
                {page_threads_sql}
                SELECT
                  page_threads.thread_key AS mailbox_thread_id,
                  page_threads.latest_matching_at AS mailbox_latest_matching_at,
                  page_threads.mailbox_order_position,
                  latest_message.user_id,
                  latest_message.message_id,
                  latest_message.gmail_thread_id,
                  latest_message.history_id,
                  thread_labels.label_ids_json,
                  latest_message.internal_date,
                  latest_message.subject,
                  latest_message.ai_title,
                  latest_message.ai_title_generated_at,
                  latest_message.sender,
                  latest_message.recipients_json,
                  latest_message.snippet,
                  latest_message.created_at,
                  latest_message.updated_at,
                  thread_stats.mailbox_message_count,
                  thread_stats.mailbox_body_ready,
                  thread_stats.mailbox_content_revision,
                  thread_stats.mailbox_attachment_count
                FROM page_threads
                JOIN LATERAL (
                  SELECT
                    messages.user_id,
                    messages.message_id,
                    messages.gmail_thread_id,
                    messages.history_id,
                    messages.internal_date,
                    messages.subject,
                    messages.ai_title,
                    messages.ai_title_generated_at,
                    messages.sender,
                    messages.recipients_json,
                    messages.snippet,
                    messages.created_at,
                    messages.updated_at
                  FROM gmail_messages AS messages
                  WHERE messages.user_id = :user_id
                    AND COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) = page_threads.thread_key
                  ORDER BY COALESCE(messages.internal_date, messages.updated_at) DESC,
                           messages.created_at ASC,
                           messages.message_id ASC
                  LIMIT 1
                ) AS latest_message ON TRUE
                JOIN LATERAL (
                  SELECT
                    COUNT(*)::INTEGER AS mailbox_message_count,
                    BOOL_AND(
                      COALESCE(messages.body_fetch_status, 'missing') IN ('fetched', 'unavailable')
                    ) AS mailbox_body_ready,
                    SUBSTRING(
                      MD5(
                        COALESCE(
                          STRING_AGG(
                            CHAR_LENGTH(messages.message_id)::TEXT || ':' ||
                            messages.message_id || ':' ||
                            GREATEST(messages.content_revision, 1)::TEXT,
                            '' ORDER BY messages.message_id
                          ),
                          ''
                        )
                      ),
                      1,
                      24
                    ) AS mailbox_content_revision,
                    COALESCE(
                      SUM(JSONB_ARRAY_LENGTH(messages.attachment_descriptors_json::jsonb)),
                      0
                    )::INTEGER AS mailbox_attachment_count
                  FROM gmail_messages AS messages
                  WHERE messages.user_id = :user_id
                    AND COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) = page_threads.thread_key
                ) AS thread_stats ON TRUE
                LEFT JOIN LATERAL (
                  SELECT COALESCE(
                    JSONB_AGG(distinct_labels.label ORDER BY distinct_labels.label),
                    '[]'::jsonb
                  )::TEXT AS label_ids_json
                  FROM (
                    SELECT DISTINCT expanded_labels.label
                    FROM gmail_messages AS label_messages
                    CROSS JOIN LATERAL JSONB_ARRAY_ELEMENTS_TEXT(
                      label_messages.label_ids_json::jsonb
                    ) AS expanded_labels(label)
                    WHERE label_messages.user_id = :user_id
                      AND COALESCE(
                        NULLIF(label_messages.gmail_thread_id, ''),
                        label_messages.message_id
                      ) = page_threads.thread_key
                  ) AS distinct_labels
                ) AS thread_labels ON TRUE
                ORDER BY {final_order_sql}
                         latest_message.message_id ASC
                """
            ),
            params,
        ).mappings().all()
    grouped: dict[str, list[GmailMessageRecord]] = {}
    order: list[str] = []
    latest_by_thread: dict[str, str] = {}
    position_by_thread: dict[str, int] = {}
    for row in rows:
        thread_key = str(row["mailbox_thread_id"])
        if thread_key not in grouped:
            grouped[thread_key] = []
            order.append(thread_key)
            latest_by_thread[thread_key] = _iso(row["mailbox_latest_matching_at"])
            if row["mailbox_order_position"] is not None:
                position_by_thread[thread_key] = int(row["mailbox_order_position"])
        grouped[thread_key].append(_mailbox_message_from_row(row))
    page_size = max(1, limit)
    visible_order = order[:page_size]
    next_cursor = None
    if len(order) > page_size and visible_order:
        last_thread = visible_order[-1]
        if order_generation_id is not None:
            next_cursor = encode_gmail_order_cursor(
                label=mailbox_label,
                generation_id=order_generation_id,
                position=position_by_thread[last_thread],
                thread_key=last_thread,
            )
        else:
            next_cursor = encode_mailbox_cursor(latest_by_thread[last_thread], last_thread)
    return MailboxThreadPage(
        threads=[(thread_key, grouped[thread_key]) for thread_key in visible_order],
        next_cursor=next_cursor,
        loaded_threads=len(visible_order),
        order_source="gmail" if order_generation_id is not None else "date",
    )


def count_mailbox_threads(
    database_url: str,
    *,
    user_id: str,
    label: str,
    since_iso: str | None = None,
    search_query: str | None = None,
    unread_only: bool = False,
) -> int:
    mailbox_label = _normalized_mailbox_label(label)
    label_clause = _mailbox_label_clause("messages", mailbox_label)
    filters = ["messages.user_id = :user_id", label_clause]
    params: dict[str, Any] = {"user_id": user_id}
    if since_iso:
        filters.append("COALESCE(messages.internal_date, messages.updated_at) >= :since_iso")
        params["since_iso"] = since_iso
    normalized_query = (search_query or "").strip()
    if normalized_query:
        filters.append(
            "(POSITION(lower(:search_query) IN lower(COALESCE(messages.subject, ''))) > 0 "
            "OR POSITION(lower(:search_query) IN lower(COALESCE(messages.sender, ''))) > 0 "
            "OR POSITION(lower(:search_query) IN lower(COALESCE(messages.snippet, ''))) > 0 "
            "OR POSITION(lower(:search_query) IN lower(COALESCE(messages.text_body, ''))) > 0 "
            "OR POSITION(lower(:search_query) IN lower(COALESCE(messages.recipients_json::text, ''))) > 0)"
        )
        params["search_query"] = normalized_query[:200]
    if unread_only:
        filters.append("messages.label_ids_json::jsonb ? 'UNREAD'")
    where_clause = "\n                    AND ".join(filters)
    with get_engine(database_url).connect() as connection:
        value = connection.execute(
            text(
                f"""
                SELECT COUNT(*)
                FROM (
                  SELECT COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) AS thread_key
                  FROM gmail_messages AS messages
                  WHERE {where_clause}
                  GROUP BY thread_key
                ) AS mailbox_threads
                """
            ),
            params,
        ).scalar_one()
    return int(value)


def encode_mailbox_cursor(latest_at: str, thread_key: str) -> str:
    payload = json.dumps({"latest_at": latest_at, "thread_key": thread_key}, separators=(",", ":"), ensure_ascii=True)
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def encode_gmail_order_cursor(*, label: str, generation_id: str, position: int, thread_key: str) -> str:
    payload = json.dumps(
        {
            "v": 2,
            "mode": "gmail_order",
            "label": _normalized_mailbox_label(label),
            "generation_id": generation_id,
            "position": position,
            "thread_key": thread_key,
        },
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_mailbox_cursor(cursor: str) -> tuple[str, str]:
    state = _decode_mailbox_cursor_state(cursor)
    if state.mode != "date" or state.latest_at is None:
        raise MailboxCursorError("Mailbox cursor uses Gmail ordering")
    return state.latest_at, state.thread_key


def _decode_mailbox_cursor_state(cursor: str) -> _MailboxCursorState:
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        thread_key = str(payload["thread_key"])
        if payload.get("mode") == "gmail_order":
            label = str(payload["label"])
            generation_id = str(payload["generation_id"])
            position = int(payload["position"])
            if payload.get("v") != 2 or position < 0:
                raise ValueError("Unsupported Gmail order cursor")
            if label != _normalized_mailbox_label(label):
                raise ValueError("Invalid mailbox label")
            if not thread_key or not generation_id:
                raise ValueError("Missing Gmail order cursor value")
            return _MailboxCursorState(
                mode="gmail_order",
                thread_key=thread_key,
                label=label,
                generation_id=generation_id,
                position=position,
            )
        latest_at = str(payload["latest_at"])
        parsed = datetime.fromisoformat(latest_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except Exception as exc:
        raise MailboxCursorError("Invalid mailbox cursor") from exc
    if not latest_at or not thread_key:
        raise MailboxCursorError("Invalid mailbox cursor")
    return _MailboxCursorState(mode="date", latest_at=parsed.isoformat(), thread_key=thread_key)


def list_mail_groups_for_gmail_threads(database_url: str, *, user_id: str, gmail_thread_ids: list[str]) -> dict[str, MailGroupRecord]:
    thread_ids = list(dict.fromkeys([thread_id for thread_id in gmail_thread_ids if thread_id]))
    if not thread_ids:
        return {}
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                WITH requested_threads AS (
                  SELECT unnest(CAST(:thread_ids AS text[])) AS thread_key
                )
                SELECT DISTINCT ON (requested_threads.thread_key)
                  requested_threads.thread_key AS mailbox_thread_id,
                  groups.*
                FROM requested_threads
                JOIN gmail_messages AS messages
                  ON messages.user_id = :user_id
                 AND COALESCE(NULLIF(messages.gmail_thread_id, ''), messages.message_id) = requested_threads.thread_key
                JOIN mail_group_members AS members
                  ON members.user_id = messages.user_id
                 AND members.gmail_message_id = messages.message_id
                JOIN mail_groups AS groups
                  ON groups.user_id = members.user_id
                 AND groups.id = members.group_id
                 AND groups.status = 'active'
                ORDER BY requested_threads.thread_key,
                         CASE
                           WHEN groups.membership_source = 'ai_batch'
                            AND groups.enrichment_status = 'ready'
                            AND (
                              groups.group_type IN ('financial_transfer', 'support_case', 'billing', 'logistics', 'account_security')
                              OR (
                                groups.group_type = 'dashboard_bundle'
                                AND groups.classification_json->>'workflow_family' IN ('financial_transfer', 'support_case', 'billing', 'logistics', 'account_security')
                              )
                            )
                            THEN 0
                           WHEN groups.group_key = 'gmail-thread:' || requested_threads.thread_key THEN 1
                           WHEN groups.membership_source = 'ai_batch' THEN 2
                           WHEN groups.group_key NOT LIKE 'gmail-thread:%' THEN 3
                           ELSE 4
                         END,
                         CASE WHEN groups.enrichment_status = 'ready' THEN 0 ELSE 1 END,
                         groups.latest_message_at DESC NULLS LAST,
                         groups.updated_at DESC
                """
            ),
            {"user_id": user_id, "thread_ids": thread_ids},
        ).mappings().all()
    return {str(row["mailbox_thread_id"]): _group_from_row(row) for row in rows}


def latest_gmail_mailbox_revision(database_url: str, *, user_id: str) -> str | None:
    with get_engine(database_url).connect() as connection:
        value = connection.execute(
            text(
                """
                SELECT MAX(revision_at)
                FROM (
                  SELECT MAX(updated_at) AS revision_at
                  FROM gmail_messages
                  WHERE user_id = :user_id
                  UNION ALL
                  SELECT MAX(refreshed_at) AS revision_at
                  FROM gmail_thread_order_state
                  WHERE user_id = :user_id
                ) AS mailbox_revisions
                """
            ),
            {"user_id": user_id},
        ).scalar_one_or_none()
    return _iso(value) if value is not None else None


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
                  messages.ai_title,
                  messages.ai_title_generated_at,
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
                  messages.body_fetch_status,
                  messages.body_fetched_at,
                  messages.body_fetch_error,
                  messages.render_doc_bytes,
                  messages.content_revision,
                  messages.attachment_descriptors_json,
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


def replace_visible_mail_projection(
    database_url: str,
    *,
    user_id: str,
    visibility: str,
    groups: list[VisibleMailGroupUpsert],
    audit_events: list[dict[str, Any]] | None = None,
) -> None:
    """Replace one visibility projection atomically.

    The membership table carries a UNIQUE(user_id, visibility, gmail_message_id)
    constraint so duplicate visible homes are rejected even if callers regress.
    """
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        connection.execute(
            text(
                """
                DELETE FROM visible_mail_groups
                WHERE user_id = :user_id AND visibility = :visibility
                """
            ),
            {"user_id": user_id, "visibility": visibility},
        )
        for group in groups:
            visible_group_id = str(uuid4())
            connection.execute(
                text(
                    """
                    INSERT INTO visible_mail_groups (
                      id, user_id, projection_key, visibility, group_kind, status,
                      canonical_entity, contact_channel, title, summary, workflow_type,
                      confidence, source_group_id, source, evidence_json,
                      latest_message_at, latest_message_id, generated_at, created_at, updated_at
                    ) VALUES (
                      :id, :user_id, :projection_key, :visibility, :group_kind, 'active',
                      :canonical_entity, :contact_channel, :title, :summary, :workflow_type,
                      :confidence, :source_group_id, :source, CAST(:evidence_json AS JSONB),
                      :latest_message_at, :latest_message_id, now(), now(), now()
                    )
                    """
                ),
                {
                    "id": visible_group_id,
                    "user_id": user_id,
                    "projection_key": group.projection_key,
                    "visibility": visibility,
                    "group_kind": group.group_kind,
                    "canonical_entity": group.canonical_entity,
                    "contact_channel": group.contact_channel,
                    "title": group.title,
                    "summary": group.summary,
                    "workflow_type": group.workflow_type,
                    "confidence": group.confidence,
                    "source_group_id": group.source_group_id,
                    "source": group.source,
                    "evidence_json": json.dumps(group.evidence, ensure_ascii=True),
                    "latest_message_at": group.latest_message_at,
                    "latest_message_id": group.latest_message_id,
                },
            )
            for message, reason, confidence in group.members:
                connection.execute(
                    text(
                        """
                        INSERT INTO visible_mail_group_members (
                          id, user_id, visible_group_id, visibility, gmail_message_id,
                          gmail_thread_id, reason, confidence, created_at
                        ) VALUES (
                          :id, :user_id, :visible_group_id, :visibility, :gmail_message_id,
                          :gmail_thread_id, :reason, :confidence, now()
                        )
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "user_id": user_id,
                        "visible_group_id": visible_group_id,
                        "visibility": visibility,
                        "gmail_message_id": message.message_id,
                        "gmail_thread_id": message.gmail_thread_id,
                        "reason": reason,
                        "confidence": confidence,
                    },
                )
        for event in audit_events or []:
            connection.execute(
                text(
                    """
                    INSERT INTO grouping_decision_audit (
                      id, user_id, source_group_id, projection_key, decision, reason,
                      evidence_json, created_at
                    ) VALUES (
                      :id, :user_id, :source_group_id, :projection_key, :decision, :reason,
                      CAST(:evidence_json AS JSONB), now()
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "user_id": user_id,
                    "source_group_id": event.get("source_group_id"),
                    "projection_key": event.get("projection_key"),
                    "decision": str(event.get("decision") or "unknown")[:80],
                    "reason": str(event.get("reason") or "")[:1000],
                    "evidence_json": json.dumps(event.get("evidence") or {}, ensure_ascii=True),
                },
            )


def list_visible_groups_for_gmail_threads(
    database_url: str,
    *,
    user_id: str,
    visibility: str,
    gmail_thread_ids: list[str],
) -> dict[str, VisibleMailGroupRecord]:
    thread_ids = list(dict.fromkeys([thread_id for thread_id in gmail_thread_ids if thread_id]))
    if not thread_ids:
        return {}
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                WITH requested_threads AS (
                  SELECT unnest(CAST(:thread_ids AS text[])) AS thread_key
                )
                SELECT DISTINCT ON (requested_threads.thread_key)
                  requested_threads.thread_key AS mailbox_thread_id,
                  groups.*
                FROM requested_threads
                JOIN visible_mail_group_members AS members
                  ON members.user_id = :user_id
                 AND members.visibility = :visibility
                 AND COALESCE(NULLIF(members.gmail_thread_id, ''), members.gmail_message_id) = requested_threads.thread_key
                JOIN visible_mail_groups AS groups
                  ON groups.user_id = members.user_id
                 AND groups.id = members.visible_group_id
                 AND groups.visibility = members.visibility
                 AND groups.status = 'active'
                ORDER BY requested_threads.thread_key,
                         CASE groups.group_kind
                           WHEN 'conversation' THEN 0
                           WHEN 'lifecycle' THEN 1
                           WHEN 'single' THEN 2
                           ELSE 3
                         END,
                         groups.confidence DESC,
                         groups.latest_message_at DESC NULLS LAST
                """
            ),
            {"user_id": user_id, "visibility": visibility, "thread_ids": thread_ids},
        ).mappings().all()
    return {str(row["mailbox_thread_id"]): _visible_group_from_row(row) for row in rows}


def list_messages_for_visible_groups(database_url: str, *, user_id: str, visible_group_ids: list[str]) -> dict[str, list[GmailMessageRecord]]:
    if not visible_group_ids:
        return {}
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT
                  members.visible_group_id AS member_group_id,
                  messages.user_id,
                  messages.message_id,
                  messages.gmail_thread_id,
                  messages.history_id,
                  messages.label_ids_json,
                  messages.internal_date,
                  messages.subject,
                  messages.ai_title,
                  messages.ai_title_generated_at,
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
                  messages.body_fetch_status,
                  messages.body_fetched_at,
                  messages.body_fetch_error,
                  messages.render_doc_bytes,
                  messages.content_revision,
                  messages.attachment_descriptors_json,
                  messages.created_at,
                  messages.updated_at
                FROM visible_mail_group_members members
                JOIN gmail_messages messages
                  ON messages.user_id = members.user_id AND messages.message_id = members.gmail_message_id
                WHERE members.user_id = :user_id AND members.visible_group_id = ANY(:group_ids)
                ORDER BY members.visible_group_id, messages.internal_date ASC NULLS LAST, messages.created_at ASC
                """
            ),
            {"user_id": user_id, "group_ids": visible_group_ids},
        ).mappings().all()
    grouped: dict[str, list[GmailMessageRecord]] = {group_id: [] for group_id in visible_group_ids}
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
    classification_version: str | None = None,
    classification: dict[str, Any] | None = None,
    classification_confidence: float = 0.0,
    ranking_reason: str | None = None,
    suppression_reason: str | None = None,
    classified_at: str | None = None,
) -> MailGroupRecord:
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
        row = connection.execute(
            text(
                """
                INSERT INTO mail_groups (
                  id, user_id, group_key, group_type, status, enrichment_status, membership_source,
                  ai_model, ai_error, ai_generated_at, ai_title, ai_summary, labels_json,
                  action_needed, action_type, priority, timing_band, dashboard_visible, latest_message_at,
                  latest_message_id, generated_from_hash, generated_at,
                  classification_version, classification_json, classification_confidence,
                  ranking_reason, suppression_reason, classified_at,
                  created_at, updated_at
                ) VALUES (
                  :id, :user_id, :group_key, :group_type, 'active', :enrichment_status, :membership_source,
                  :ai_model, :ai_error, :ai_generated_at, :ai_title, :ai_summary, :labels_json,
                  :action_needed, :action_type, :priority, :timing_band, :dashboard_visible, :latest_message_at,
                  :latest_message_id, :generated_from_hash, :generated_at,
                  :classification_version, CAST(:classification_json AS JSONB), :classification_confidence,
                  :ranking_reason, :suppression_reason, :classified_at,
                  now(), now()
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
                  classification_version = excluded.classification_version,
                  classification_json = excluded.classification_json,
                  classification_confidence = excluded.classification_confidence,
                  ranking_reason = excluded.ranking_reason,
                  suppression_reason = excluded.suppression_reason,
                  classified_at = excluded.classified_at,
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
                "classification_version": classification_version,
                "classification_json": json.dumps(classification or {}, ensure_ascii=True),
                "classification_confidence": classification_confidence,
                "ranking_reason": ranking_reason,
                "suppression_reason": suppression_reason,
                "classified_at": classified_at,
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
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
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
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
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
    engine = get_engine(database_url)
    transaction = (
        engine.begin()
        if entity_id.startswith("manual-task:")
        else user_mail_write_transaction(engine, user_id=user_id)
    )
    with transaction as connection:
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


def list_pending_mail_groups(database_url: str, *, user_id: str, limit: int = 10, preferred_group_ids: list[str] | None = None) -> list[MailGroupRecord]:
    preferred = list(dict.fromkeys([group_id for group_id in (preferred_group_ids or []) if group_id]))
    with get_engine(database_url).connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT *
                FROM mail_groups AS groups
                WHERE groups.user_id = :user_id
                  AND groups.status = 'active'
                  AND (
                    groups.enrichment_status = 'pending'
                    OR (
                      groups.enrichment_status = 'ready'
                      AND groups.id = ANY(:preferred_group_ids)
                      AND EXISTS (
                        SELECT 1
                        FROM mail_group_members AS members
                        JOIN gmail_messages AS messages
                          ON messages.user_id = members.user_id
                         AND messages.message_id = members.gmail_message_id
                        WHERE members.user_id = groups.user_id
                          AND members.group_id = groups.id
                          AND messages.ai_title IS NULL
                      )
                    )
                  )
                ORDER BY
                  CASE WHEN groups.id = ANY(:preferred_group_ids) THEN 0 ELSE 1 END,
                  groups.latest_message_at DESC NULLS LAST,
                  groups.priority DESC,
                  groups.updated_at ASC
                LIMIT :limit
                """
            ),
            {"user_id": user_id, "limit": limit, "preferred_group_ids": preferred},
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


def latest_mail_group_ai_error(database_url: str, *, user_id: str) -> str | None:
    with get_engine(database_url).connect() as connection:
        value = connection.execute(
            text(
                """
                SELECT ai_error
                FROM mail_groups
                WHERE user_id = :user_id
                  AND status = 'active'
                  AND ai_error IS NOT NULL
                  AND ai_error <> ''
                ORDER BY ai_generated_at DESC NULLS LAST, updated_at DESC
                LIMIT 1
                """
            ),
            {"user_id": user_id},
        ).scalar()
    return str(value) if value else None


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
    with user_mail_write_transaction(get_engine(database_url), user_id=user_id) as connection:
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


def _normalized_mailbox_label(label: str) -> str:
    normalized = label.strip().lower()
    return normalized if normalized in {"inbox", "sent", "drafts", "spam", "trash", "archive", "starred", "all"} else "inbox"


def _mailbox_label_clause(alias: str, label: str) -> str:
    label_json = f"{alias}.label_ids_json::jsonb"
    if label == "all":
        return f"NOT ({label_json} ? 'SPAM') AND NOT ({label_json} ? 'TRASH')"
    if label == "inbox":
        return f"{label_json} ? 'INBOX'"
    if label == "sent":
        return f"{label_json} ? 'SENT'"
    if label == "drafts":
        return f"{label_json} ? 'DRAFT'"
    if label == "spam":
        return f"{label_json} ? 'SPAM'"
    if label == "trash":
        return f"{label_json} ? 'TRASH'"
    if label == "starred":
        return f"{label_json} ? 'STARRED'"
    if label == "archive":
        return (
            f"NOT ({label_json} ? 'INBOX') "
            f"AND NOT ({label_json} ? 'SENT') "
            f"AND NOT ({label_json} ? 'DRAFT') "
            f"AND NOT ({label_json} ? 'SPAM') "
            f"AND NOT ({label_json} ? 'TRASH')"
        )
    return "TRUE"


def _gmail_attachment_descriptors(raw_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract only reader-visible attachment metadata from a Gmail payload."""

    payload = raw_payload.get("payload") if isinstance(raw_payload, dict) else None
    if not isinstance(payload, dict):
        return []

    descriptors: list[dict[str, Any]] = []

    def visit(part: dict[str, Any], path: tuple[int, ...]) -> None:
        body = part.get("body") if isinstance(part.get("body"), dict) else {}
        attachment_id = body.get("attachmentId") if isinstance(body, dict) else None
        if isinstance(attachment_id, str) and attachment_id:
            headers: dict[str, str] = {}
            for header in part.get("headers", []) if isinstance(part.get("headers"), list) else []:
                if not isinstance(header, dict):
                    continue
                name = str(header.get("name") or "").strip().lower()
                value = str(header.get("value") or "").strip()
                if name and value:
                    headers[name] = value
            disposition = headers.get("content-disposition", "").lower()
            filename = str(part.get("filename") or "").strip()
            is_inline = disposition.startswith("inline") or (
                not filename and bool(headers.get("content-id"))
            )
            is_downloadable = bool(filename) or disposition.startswith("attachment")
            if not is_inline and is_downloadable:
                raw_size = body.get("size") if isinstance(body, dict) else 0
                try:
                    size = max(0, int(raw_size or 0))
                except (TypeError, ValueError):
                    size = 0
                descriptors.append(
                    {
                        "filename": filename or f"attachment-{len(descriptors) + 1}",
                        "mime_type": str(part.get("mimeType") or "application/octet-stream"),
                        "size": size,
                        "attachment_id": attachment_id,
                        "part_id": str(part.get("partId") or ".".join(str(index) for index in path)),
                    }
                )
        children = part.get("parts")
        if isinstance(children, list):
            for index, child in enumerate(children):
                if isinstance(child, dict):
                    visit(child, (*path, index))

    visit(payload, (0,))
    return descriptors


def _message_params(message: GmailMessageRecord) -> dict[str, Any]:
    body_fetch_status = message.body_fetch_status or _body_fetch_status_for_message(message)
    body_fetched_at = message.body_fetched_at
    if body_fetch_status == "fetched" and body_fetched_at is None:
        body_fetched_at = datetime.now(timezone.utc).isoformat()
    attachment_descriptors = list(message.attachment_descriptors)
    if not attachment_descriptors and message.raw_payload:
        attachment_descriptors = _gmail_attachment_descriptors(message.raw_payload)
    descriptors_ready = bool(
        message.attachment_descriptors_ready
        or body_fetch_status == "fetched"
    )
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
        "body_fetch_status": body_fetch_status,
        "body_fetched_at": body_fetched_at,
        "body_fetch_error": message.body_fetch_error,
        "render_doc_bytes": message.render_doc_bytes or len(message.html_render_document or ""),
        "ai_title": message.ai_title,
        "ai_title_generated_at": message.ai_title_generated_at,
        "content_revision": max(1, int(message.content_revision or 1)),
        "attachment_descriptors_json": json.dumps(
            attachment_descriptors,
            ensure_ascii=True,
        ),
        "attachment_descriptors_ready": descriptors_ready,
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
        ai_title=str(row["ai_title"]) if "ai_title" in row and row["ai_title"] is not None else None,
        ai_title_generated_at=_iso(row["ai_title_generated_at"]) if "ai_title_generated_at" in row and row["ai_title_generated_at"] is not None else None,
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
        body_fetch_status=str(row["body_fetch_status"]) if row.get("body_fetch_status") is not None else None,
        body_fetched_at=_iso(row["body_fetched_at"]) if row.get("body_fetched_at") is not None else None,
        body_fetch_error=str(row["body_fetch_error"]) if row.get("body_fetch_error") is not None else None,
        render_doc_bytes=int(row["render_doc_bytes"] or 0) if row.get("render_doc_bytes") is not None else 0,
        content_revision=(
            max(1, int(row["content_revision"] or 1))
            if row.get("content_revision") is not None
            else 1
        ),
        attachment_descriptors=_attachment_descriptors_from_json(
            row.get("attachment_descriptors_json")
        ),
        attachment_descriptors_ready=bool(
            row.get("attachment_descriptors_ready", False)
        ),
    )


def _mailbox_message_from_row(row) -> GmailMessageRecord:
    """Decode one constant-width aggregate projection for one Gmail thread."""

    mailbox_body_ready = bool(row["mailbox_body_ready"])
    mailbox_content_revision = str(row["mailbox_content_revision"])

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
        headers={},
        snippet=str(row["snippet"]) if row["snippet"] is not None else None,
        raw_payload={},
        html_body_sanitized=None,
        html_render_document=None,
        text_body=None,
        extracted_signals={},
        body_hash=mailbox_content_revision,
        created_at=_iso(row["created_at"]),
        updated_at=_iso(row["updated_at"]),
        body_fetch_status="fetched" if mailbox_body_ready else "missing",
        body_fetched_at=None,
        body_fetch_error=None,
        render_doc_bytes=0,
        ai_title=str(row["ai_title"]) if row.get("ai_title") is not None else None,
        ai_title_generated_at=(
            _iso(row["ai_title_generated_at"])
            if row.get("ai_title_generated_at") is not None
            else None
        ),
        content_revision=1,
        attachment_descriptors=[],
        attachment_descriptors_ready=True,
        mailbox_message_count=max(1, int(row["mailbox_message_count"] or 1)),
        mailbox_body_ready=mailbox_body_ready,
        mailbox_content_revision=mailbox_content_revision,
        mailbox_attachment_count=max(0, int(row["mailbox_attachment_count"] or 0)),
    )


def _body_fetch_status_for_message(message: GmailMessageRecord) -> str:
    if _payload_has_body_data(message.raw_payload) or message.html_render_document or message.html_body_sanitized:
        return "fetched"
    return "missing"


def _payload_has_body_data(payload: dict[str, Any]) -> bool:
    def visit(part: Any) -> bool:
        if not isinstance(part, dict):
            return False
        body = part.get("body")
        if isinstance(body, dict) and isinstance(body.get("data"), str) and body.get("data"):
            return True
        parts = part.get("parts")
        if isinstance(parts, list):
            return any(visit(item) for item in parts)
        return False

    return visit(payload.get("payload") if isinstance(payload, dict) else payload)


def _pending_thread_action_from_row(row) -> PendingThreadActionRecord:
    raw_previous_labels = _json_dict(row.get("previous_labels_json"))
    previous_labels = {
        str(message_id): [str(label) for label in labels if str(label).strip()]
        for message_id, labels in raw_previous_labels.items()
        if isinstance(labels, list)
    }
    return PendingThreadActionRecord(
        server_action_id=str(row["server_action_id"]),
        client_action_id=str(row["client_action_id"]),
        user_id=str(row["user_id"]),
        mailbox_thread_id=str(row["mailbox_thread_id"]),
        target_message_id=str(row["target_message_id"]) if row.get("target_message_id") is not None else None,
        action=str(row["action"]),
        state=str(row["state"]),
        created_at=_iso(row["created_at"]),
        queued_at=_iso(row["queued_at"]),
        applied_at=_iso(row["applied_at"]) if row["applied_at"] is not None else None,
        error=str(row["error"]) if row["error"] is not None else None,
        updated_at=_iso(row["updated_at"]),
        previous_labels=previous_labels,
    )


def _pending_send_from_row(row) -> PendingSendRecord:
    return PendingSendRecord(
        server_send_id=str(row["server_send_id"]),
        client_send_id=str(row["client_send_id"]),
        user_id=str(row["user_id"]),
        send_type=str(row["send_type"]),
        mailbox_thread_id=str(row["mailbox_thread_id"]) if row["mailbox_thread_id"] is not None else None,
        gmail_thread_id=str(row["gmail_thread_id"]) if row["gmail_thread_id"] is not None else None,
        to=_json_list(row["to_json"]),
        cc=_json_list(row["cc_json"]),
        bcc=_json_list(row["bcc_json"]),
        subject=str(row["subject"]),
        body_text=str(row["body_text"]),
        body_html=str(row["body_html"]) if row["body_html"] is not None else None,
        headers=_json_dict(row["headers_json"]),
        state=str(row["state"]),
        created_at=_iso(row["created_at"]),
        queued_at=_iso(row["queued_at"]),
        sent_at=_iso(row["sent_at"]) if row["sent_at"] is not None else None,
        gmail_message_id=str(row["gmail_message_id"]) if row["gmail_message_id"] is not None else None,
        error=str(row["error"]) if row["error"] is not None else None,
        updated_at=_iso(row["updated_at"]),
        attachments=_json_list_of_dicts(row.get("attachments_json")),
        request_hash=str(row.get("request_hash") or ""),
    )


def _client_draft_from_row(row) -> ClientDraftRecord:
    return ClientDraftRecord(
        user_id=str(row["user_id"]),
        client_draft_id=str(row["client_draft_id"]),
        gmail_draft_id=str(row["gmail_draft_id"]) if row["gmail_draft_id"] is not None else None,
        gmail_message_id=str(row["gmail_message_id"]) if row["gmail_message_id"] is not None else None,
        gmail_thread_id=str(row["gmail_thread_id"]) if row["gmail_thread_id"] is not None else None,
        content_hash=str(row["content_hash"] or ""),
        state=str(row["state"]),
        last_client_send_id=str(row["last_client_send_id"]) if row["last_client_send_id"] is not None else None,
        sent_message_id=str(row["sent_message_id"]) if row["sent_message_id"] is not None else None,
        error=str(row["error"]) if row["error"] is not None else None,
        created_at=_iso(row["created_at"]),
        saved_at=_iso(row["saved_at"]) if row["saved_at"] is not None else None,
        updated_at=_iso(row["updated_at"]),
    )


def _json_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _json_list_of_dicts(value: Any) -> list[dict[str, str]]:
    if isinstance(value, list):
        parsed = value
    else:
        try:
            parsed = json.loads(value or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
    if not isinstance(parsed, list):
        return []
    return [
        {str(key): str(item_value) for key, item_value in item.items()}
        for item in parsed
        if isinstance(item, dict)
    ]


def _attachment_descriptors_from_json(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        parsed = value
    else:
        try:
            parsed = json.loads(value or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
    if not isinstance(parsed, list):
        return []
    descriptors: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        attachment_id = str(item.get("attachment_id") or "").strip()
        if not attachment_id:
            continue
        try:
            size = max(0, int(item.get("size") or 0))
        except (TypeError, ValueError):
            size = 0
        descriptors.append(
            {
                "filename": str(item.get("filename") or "").strip(),
                "mime_type": str(item.get("mime_type") or "application/octet-stream"),
                "size": size,
                "attachment_id": attachment_id,
                "part_id": str(item.get("part_id") or "").strip(),
            }
        )
    return descriptors


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _group_from_row(row) -> MailGroupRecord:
    classification_payload = row["classification_json"] if "classification_json" in row else {}
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
        classification_version=str(row["classification_version"]) if "classification_version" in row and row["classification_version"] is not None else None,
        classification=_json_dict(classification_payload),
        classification_confidence=float(row["classification_confidence"]) if "classification_confidence" in row and row["classification_confidence"] is not None else 0.0,
        ranking_reason=str(row["ranking_reason"]) if "ranking_reason" in row and row["ranking_reason"] is not None else None,
        suppression_reason=str(row["suppression_reason"]) if "suppression_reason" in row and row["suppression_reason"] is not None else None,
        classified_at=_iso(row["classified_at"]) if "classified_at" in row and row["classified_at"] is not None else None,
    )


def _visible_group_from_row(row) -> VisibleMailGroupRecord:
    evidence_payload = row["evidence_json"] if "evidence_json" in row else {}
    if isinstance(evidence_payload, str):
        try:
            evidence = json.loads(evidence_payload)
        except json.JSONDecodeError:
            evidence = {}
    else:
        evidence = dict(evidence_payload or {})
    return VisibleMailGroupRecord(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        projection_key=str(row["projection_key"]),
        visibility=str(row["visibility"]),
        group_kind=str(row["group_kind"]),
        status=str(row["status"]),
        canonical_entity=str(row["canonical_entity"]),
        contact_channel=str(row["contact_channel"]) if row["contact_channel"] is not None else None,
        title=str(row["title"]),
        summary=str(row["summary"]),
        workflow_type=str(row["workflow_type"]),
        confidence=float(row["confidence"] or 0),
        source_group_id=str(row["source_group_id"]) if row["source_group_id"] is not None else None,
        source=str(row["source"]),
        evidence=evidence,
        latest_message_at=_iso(row["latest_message_at"]) if row["latest_message_at"] is not None else None,
        latest_message_id=str(row["latest_message_id"]) if row["latest_message_id"] is not None else None,
        generated_at=_iso(row["generated_at"]),
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


def _mailbox_event_from_row(row) -> MailboxEventRecord:
    payload = row["payload_json"]
    if isinstance(payload, str):
        try:
            payload_value = json.loads(payload)
        except json.JSONDecodeError:
            payload_value = {}
    elif isinstance(payload, dict):
        payload_value = payload
    else:
        payload_value = {}
    return MailboxEventRecord(
        id=int(row["id"]),
        user_id=str(row["user_id"]),
        event_type=str(row["event_type"]),
        mailbox_label=str(row["mailbox_label"]) if row["mailbox_label"] is not None else None,
        payload=payload_value,
        created_at=_iso(row["created_at"]),
    )


def _state_from_row(row) -> GmailImportState:
    return GmailImportState(
        user_id=str(row["user_id"]),
        last_history_id=str(row["last_history_id"]) if row["last_history_id"] is not None else None,
        history_cursor_authoritative=(
            bool(row["history_cursor_authoritative"])
            if "history_cursor_authoritative" in row
            else False
        ),
        full_backfill_cursor=str(row["full_backfill_cursor"]) if row["full_backfill_cursor"] is not None else None,
        full_backfill_started_at=_iso(row["full_backfill_started_at"]) if "full_backfill_started_at" in row and row["full_backfill_started_at"] is not None else None,
        full_backfill_completed_at=_iso(row["full_backfill_completed_at"]) if "full_backfill_completed_at" in row and row["full_backfill_completed_at"] is not None else None,
        first_batch_imported_at=_iso(row["first_batch_imported_at"]) if row["first_batch_imported_at"] is not None else None,
        first_groups_ready_at=_iso(row["first_groups_ready_at"]) if row["first_groups_ready_at"] is not None else None,
        first_dashboard_ready_at=_iso(row["first_dashboard_ready_at"]) if row["first_dashboard_ready_at"] is not None else None,
        last_import_started_at=_iso(row["last_import_started_at"]) if row["last_import_started_at"] is not None else None,
        last_import_completed_at=_iso(row["last_import_completed_at"]) if row["last_import_completed_at"] is not None else None,
        last_delta_sync_at=(
            _iso(row["last_delta_sync_at"])
            if "last_delta_sync_at" in row and row["last_delta_sync_at"] is not None
            else None
        ),
        last_sync_error=str(row["last_sync_error"]) if row["last_sync_error"] is not None else None,
        gmail_watch_history_id=str(row["gmail_watch_history_id"]) if "gmail_watch_history_id" in row and row["gmail_watch_history_id"] is not None else None,
        gmail_watch_expiration_at=_iso(row["gmail_watch_expiration_at"]) if "gmail_watch_expiration_at" in row and row["gmail_watch_expiration_at"] is not None else None,
        gmail_watch_started_at=_iso(row["gmail_watch_started_at"]) if "gmail_watch_started_at" in row and row["gmail_watch_started_at"] is not None else None,
        gmail_watch_error=str(row["gmail_watch_error"]) if "gmail_watch_error" in row and row["gmail_watch_error"] is not None else None,
        reconcile_generation=str(row["reconcile_generation"]) if "reconcile_generation" in row and row["reconcile_generation"] is not None else None,
        reconcile_cursor=str(row["reconcile_cursor"]) if "reconcile_cursor" in row and row["reconcile_cursor"] is not None else None,
        reconcile_baseline_history_id=(
            str(row["reconcile_baseline_history_id"])
            if "reconcile_baseline_history_id" in row and row["reconcile_baseline_history_id"] is not None
            else None
        ),
        reconcile_started_at=_iso(row["reconcile_started_at"]) if "reconcile_started_at" in row and row["reconcile_started_at"] is not None else None,
        updated_at=_iso(row["updated_at"]),
        sync_generation=str(row["sync_generation"]) if "sync_generation" in row and row["sync_generation"] is not None else None,
        phase=str(row["phase"]) if "phase" in row and row["phase"] is not None else None,
        initial_target_count=int(row["initial_target_count"] or 0) if "initial_target_count" in row else 0,
        initial_metadata_count=int(row["initial_metadata_count"] or 0) if "initial_metadata_count" in row else 0,
        initial_body_target_count=int(row["initial_body_target_count"] or 0) if "initial_body_target_count" in row else 0,
        initial_body_ready_count=int(row["initial_body_ready_count"] or 0) if "initial_body_ready_count" in row else 0,
        history_metadata_count=int(row["history_metadata_count"] or 0) if "history_metadata_count" in row else 0,
        history_body_ready_count=int(row["history_body_ready_count"] or 0) if "history_body_ready_count" in row else 0,
        estimated_total_count=int(row["estimated_total_count"] or 0) if "estimated_total_count" in row else 0,
        initial_window_complete=bool(row["initial_window_complete"]) if "initial_window_complete" in row else False,
        history_metadata_complete=bool(row["history_metadata_complete"]) if "history_metadata_complete" in row else False,
        history_body_complete=bool(row["history_body_complete"]) if "history_body_complete" in row else False,
        last_progress_at=_iso(row["last_progress_at"]) if "last_progress_at" in row and row["last_progress_at"] is not None else None,
        attachment_descriptors_complete=(
            bool(row["attachment_descriptors_complete"])
            if "attachment_descriptors_complete" in row
            else True
        ),
    )


def _initial_window_entry_from_row(row) -> GmailInitialWindowEntry:
    return GmailInitialWindowEntry(
        user_id=str(row["user_id"]),
        generation_id=str(row["generation_id"]),
        gmail_thread_id=str(row["gmail_thread_id"]),
        position=int(row["position"]),
        message_count=int(row["message_count"] or 0),
        metadata_ready_at=_iso(row["metadata_ready_at"]) if row["metadata_ready_at"] is not None else None,
        body_ready_at=_iso(row["body_ready_at"]) if row["body_ready_at"] is not None else None,
    )


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)
