from __future__ import annotations

"""Raw Gmail mailbox product pipeline for the no-AI release."""

from collections import defaultdict, OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr
import hashlib
import json
import logging
import re
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from app.core.config import Settings
from app.db.jobs import count_active_jobs, enqueue_job, get_queue_health
from app.db.mail_groups import (
    EntityOutcomeRecord,
    GmailMessageRecord,
    MailGroupRecord,
    ManualTaskRecord,
    VisibleMailGroupRecord,
    activate_existing_gmail_progressive_sync,
    count_mailbox_threads,
    count_mail_groups,
    count_mail_groups_by_enrichment_status,
    count_dashboard_mail_groups,
    count_pending_thread_actions,
    count_ready_mail_groups_since,
    get_app_session_snapshot,
    get_import_state,
    gmail_history_cursor_is_authoritative,
    get_gmail_thread_message_page,
    get_latest_entity_outcomes,
    latest_mail_group_ai_error,
    latest_gmail_mailbox_revision,
    get_mail_group_by_key,
    get_mail_group_detail,
    list_dashboard_mail_groups,
    list_group_messages,
    list_gmail_initial_window_positions,
    list_gmail_initial_window_entries_needing_body_fetch,
    list_mailbox_thread_page,
    list_mail_groups_for_gmail_threads,
    list_mail_groups,
    list_messages_by_ids,
    list_messages_for_gmail_thread,
    list_messages_for_visible_groups,
    list_messages_for_groups,
    list_open_manual_tasks,
    list_recent_messages,
    oldest_imported_message_at,
    replace_group_members,
    replace_visible_mail_projection,
    update_gmail_message_ai_titles,
    upsert_app_session_snapshot,
    upsert_gmail_messages,
    upsert_mail_group,
    user_can_write_gmail,
    list_visible_groups_for_gmail_threads,
)
from app.schemas.domain import (
    AppSessionResponse,
    AppSessionSyncState,
    AppSessionUser,
    AttentionItem,
    AttentionItemDetail,
    DashboardBriefing,
    DashboardProfile,
    DashboardResponse,
    FeedResponse,
    GmailThreadChildRow,
    GmailThreadRow,
    GmailThreadSection,
    GoogleAuthState,
    MailboxLabel,
    MailboxRealtimeStateResponse,
    MailboxResponse,
    MailboxSyncStateResponse,
    PostLoginReadinessResponse,
    ThreadAttachment,
    ThreadMessage,
    ThreadReaderResponse,
)
from app.services.email_extraction import (
    build_thread_message_reader,
    compact_text,
    has_persisted_renderable_body,
    html_body_for_reader,
    html_render_document_for_reader,
    parse_gmail_message,
    sender_domain,
)
from app.services.grouping_projection import build_inbox_projection
from app.services.attention_classifier import (
    attention_enrichment_payload,
    deterministic_classification,
    facts_to_json,
    is_status_noise_classification,
    is_terminal_classification,
    workflow_cluster_key,
)
from app.services.integrations.google import (
    GMAIL_FULL_SCOPE,
    GOOGLE_CONTACTS_READ_SCOPE,
    check_user_google_credentials,
    missing_google_scopes,
)
from app.services.contact_avatars import normalize_contact_email, resolve_contact_avatar_asset_ids
from app.services.mailbox_events import GMAIL_PUBSUB_RECEIVED, latest_event
from app.services.remote_images import rewrite_external_image_sources

FIRST_BATCH_SIZE = 25
BACKFILL_BATCH_SIZE = 30
MAILBOX_REBUILD_LIMIT = 5000
MAIL_GROUP_ENRICH_BATCH_SIZE = 25
DASHBOARD_DAYS = 14
RECENT_VISIBLE_DAYS = 90
APP_SESSION_PROJECTION_VERSION = "20260609_visible_group_projection_v1"
GENERIC_SENDER_SLUGS = {"alert", "alerts", "info", "mail", "no-reply", "noreply", "notification", "notifications", "support", "team"}
MAILBOX_DISPLAY_CLUSTER_WORKFLOW_TYPES = {
    "financial_transfer",
    "support_case",
    "billing",
    "logistics",
    "account_security",
    "application",
    "newsletter",
    "marketing",
}


def _full_import_status(
    state: Any,
    *,
    active_backfill_jobs: int,
    active_reconciliation_jobs: int,
) -> tuple[bool, bool, str | None]:
    """Return truthful full-import running/completed state."""
    if state is None:
        return bool(active_backfill_jobs or active_reconciliation_jobs), False, None
    backfill_completed_at = getattr(state, "full_backfill_completed_at", None)
    reconciliation_running = bool(
        getattr(state, "reconcile_generation", None)
        or active_reconciliation_jobs
    )
    backfill_running = bool(
        getattr(state, "first_batch_imported_at", None)
        and not backfill_completed_at
        and (getattr(state, "full_backfill_cursor", None) or active_backfill_jobs)
    )
    completed = bool(
        backfill_completed_at
        and gmail_history_cursor_is_authoritative(state)
        and not reconciliation_running
    )
    return (
        reconciliation_running or backfill_running,
        completed,
        str(backfill_completed_at) if completed else None,
    )


def _sync_progress_kwargs(state: Any) -> dict[str, Any]:
    """Project additive progress fields from old or new import-state rows."""
    generation = getattr(state, "sync_generation", None) if state else None
    if not generation:
        return {
            "sync_generation": None,
            "phase": None,
            "initial_target_count": None,
            "initial_metadata_count": None,
            "initial_body_target_count": None,
            "initial_body_ready_count": None,
            "history_metadata_count": None,
            "history_body_ready_count": None,
            "estimated_total_count": None,
            "initial_window_complete": None,
            "history_metadata_complete": None,
            "history_body_complete": None,
            "last_progress_at": None,
        }
    return {
        "sync_generation": str(generation),
        "phase": getattr(state, "phase", None),
        "initial_target_count": int(getattr(state, "initial_target_count", 0) or 0),
        "initial_metadata_count": int(getattr(state, "initial_metadata_count", 0) or 0),
        "initial_body_target_count": int(getattr(state, "initial_body_target_count", 0) or 0),
        "initial_body_ready_count": int(getattr(state, "initial_body_ready_count", 0) or 0),
        "history_metadata_count": int(getattr(state, "history_metadata_count", 0) or 0),
        "history_body_ready_count": int(getattr(state, "history_body_ready_count", 0) or 0),
        "estimated_total_count": int(getattr(state, "estimated_total_count", 0) or 0),
        "initial_window_complete": bool(getattr(state, "initial_window_complete", False)),
        "history_metadata_complete": bool(getattr(state, "history_metadata_complete", False)),
        "history_body_complete": bool(getattr(state, "history_body_complete", False)),
        "last_progress_at": getattr(state, "last_progress_at", None),
    }


MAILBOX_DISPLAY_CLUSTER_PREFIX = "mailbox-cluster:"
TRANSFER_TOPIC_RE = re.compile(r"(?i)\b(remittance|wire|fx|forex|trade)\b")
TRAVEL_TOPIC_RE = re.compile(r"(?i)\b(ride|trip|reservation|bus|flight)\b")
logger = logging.getLogger(__name__)
TOPIC_STOP_WORDS = {
    "account",
    "application",
    "created",
    "complete",
    "congratulations",
    "from",
    "received",
    "reference",
    "request",
    "save",
    "successfully",
    "the",
    "this",
    "to",
    "update",
    "welcome",
    "your",
}


def _ai_grouping_enabled(settings: Settings) -> bool:
    # This branch ships the raw Gmail mailbox only; AI grouping is not a runtime
    # option even if a stale environment variable is present.
    return False


def _without_legacy_ai_dashboard(dashboard: DashboardResponse) -> DashboardResponse:
    """Remove generated mail-group content from a stored pre-no-AI snapshot."""

    def keep(item: AttentionItem) -> bool:
        return not (
            item.source == "gmail"
            or item.gmail_thread_id is not None
            or item.id.startswith("mail-group:")
            or item.trace_id.startswith("mail-group:")
        )

    feed = FeedResponse(
        now=[item for item in dashboard.feed.now if keep(item)],
        today=[item for item in dashboard.feed.today if keep(item)],
        worth_knowing=[item for item in dashboard.feed.worth_knowing if keep(item)],
    )
    items = [*feed.now, *feed.today, *feed.worth_knowing]
    runtime_status = {
        key: value
        for key, value in dashboard.runtime_status.items()
        if key not in {"ai_groups_ready", "classification_debug", "last_ai_error"}
    }
    if any(item.source == "manual" for item in items):
        runtime_status["feed_source"] = "manual_tasks"
    elif items:
        runtime_status["feed_source"] = "non_ai_items"
    else:
        runtime_status["feed_source"] = "empty"
    return dashboard.model_copy(
        update={
            "briefing": _dashboard_briefing(profile=dashboard.profile, feed=feed),
            "feed": feed,
            "runtime_status": runtime_status,
        }
    )


def _mailbox_contains_legacy_ai_content(mailbox: MailboxResponse) -> bool:
    """Detect snapshots that must be rebuilt from canonical Gmail threads."""

    for section in mailbox.sections:
        for row in section.rows:
            if (
                row.ai_group_id is not None
                or row.ai_title is not None
                or row.ai_summary is not None
                or row.presentation_status in {"ai_ready", "ai_pending"}
                or _is_mailbox_display_cluster_id(row.thread_id)
                or any(child.ai_title is not None for child in row.children)
            ):
                return True
    return False


@dataclass(frozen=True)
class MailboxDisplayClusterEntry:
    thread_id: str
    messages: list[GmailMessageRecord]
    row: GmailThreadRow
    cluster_key: str | None


def _google_auth_state(settings: Settings, *, user_id: str) -> GoogleAuthState:
    connected = user_can_write_gmail(str(settings.database_path), user_id=user_id)
    missing_scopes = missing_google_scopes(settings, user_id=user_id, required_scopes=[GMAIL_FULL_SCOPE]) if connected else []
    reauth_required = connected and bool(missing_scopes)
    missing_optional_scopes = (
        missing_google_scopes(settings, user_id=user_id, required_scopes=[GOOGLE_CONTACTS_READ_SCOPE])
        if connected
        else []
    )
    return GoogleAuthState(
        available=settings.google_configured,
        connected=connected,
        connect_url=f"{settings.backend_origin}/auth/google" if not connected or reauth_required else None,
        can_send_mail=connected and not missing_scopes,
        missing_scopes=missing_scopes,
        contact_photos_available=connected and not missing_optional_scopes,
        missing_optional_scopes=missing_optional_scopes,
        reauth_required=reauth_required,
        error="Google needs full mail permission. Please sign in with Google again." if reauth_required else None,
    )


def enqueue_first_run(settings: Settings, *, user_id: str) -> str:
    from app.db.account_scope import active_gmail_account_id
    account_id = active_gmail_account_id() or user_id
    job = enqueue_job(
        str(settings.database_path),
        kind="gmail_import_batch",
        queue="critical",
        user_id=user_id,
        gmail_account_id=account_id,
        dedupe_key=f"first-run:{user_id}:{account_id}",
        priority=100,
        payload={"user_id": user_id, "gmail_account_id": account_id, "batch_size": FIRST_BATCH_SIZE, "first_run": True},
    )
    return job.id


def enqueue_mailbox_sync(settings: Settings, *, user_id: str) -> str:
    from app.db.account_scope import active_gmail_account_id
    account_id = active_gmail_account_id() or user_id
    state = get_import_state(str(settings.database_path), user_id=user_id)
    if gmail_history_cursor_is_authoritative(state):
        job = enqueue_job(
            str(settings.database_path),
            kind="gmail_delta_sync",
            queue="critical",
            user_id=user_id,
            gmail_account_id=account_id,
            dedupe_key=f"gmail-delta-sync:{user_id}:{account_id}",
            priority=85,
            payload={"user_id": user_id, "gmail_account_id": account_id, "batch_size": 100, "source": "manual"},
        )
        ensure_background_import_work(settings, user_id=user_id)
        return job.id

    job = enqueue_job(
        str(settings.database_path),
        kind="gmail_import_batch",
        queue="default",
        user_id=user_id,
        gmail_account_id=account_id,
        dedupe_key=f"mailbox-sync:{user_id}:{account_id}",
        priority=20,
        payload={"user_id": user_id, "gmail_account_id": account_id, "batch_size": BACKFILL_BATCH_SIZE, "first_run": False},
    )
    ensure_background_import_work(settings, user_id=user_id)
    return job.id


def ensure_background_import_work(settings: Settings, *, user_id: str) -> None:
    database_url = str(settings.database_path)
    if not user_can_write_gmail(database_url, user_id=user_id):
        return
    state = get_import_state(database_url, user_id=user_id)
    if (
        state is not None
        and state.first_batch_imported_at
        and not getattr(state, "sync_generation", None)
    ):
        generation_id = str(getattr(state, "reconcile_generation", None) or uuid4())
        state = activate_existing_gmail_progressive_sync(
            database_url,
            user_id=user_id,
            generation_id=generation_id,
            history_metadata_complete=bool(
                getattr(state, "full_backfill_completed_at", None)
            ),
        ) or state
    reconcile_generation = str(getattr(state, "reconcile_generation", None) or "")
    sync_generation = str(getattr(state, "sync_generation", None) or "")
    if (
        state
        and sync_generation
        and not bool(getattr(state, "history_body_complete", False))
        and not count_active_jobs(
            database_url,
            user_id=user_id,
            kinds=["gmail_body_fetch"],
        )
    ):
        for entry in list_gmail_initial_window_entries_needing_body_fetch(
            database_url,
            user_id=user_id,
            generation_id=sync_generation,
            limit=100,
        ):
            enqueue_job(
                database_url,
                kind="gmail_body_fetch",
                queue="reader",
                user_id=user_id,
                dedupe_key=f"gmail-body-fetch-thread:{user_id}:{entry.gmail_thread_id}",
                priority=90 if entry.position < 25 else 60,
                payload={
                    "user_id": user_id,
                    "gmail_thread_id": entry.gmail_thread_id,
                    "sync_generation": sync_generation,
                },
                wake_existing=False,
            )
    if (
        reconcile_generation
        and sync_generation == reconcile_generation
        and not bool(getattr(state, "initial_window_complete", False))
    ):
        active_initial_jobs = count_active_jobs(
            database_url,
            user_id=user_id,
            kinds=["gmail_import_batch"],
        )
        if not active_initial_jobs:
            enqueue_job(
                database_url,
                kind="gmail_import_batch",
                queue="critical",
                user_id=user_id,
                dedupe_key=f"first-run:{user_id}:{sync_generation}:resume",
                priority=100,
                payload={"user_id": user_id, "batch_size": FIRST_BATCH_SIZE, "first_run": True},
            )
        return
    active_reconciliations = count_active_jobs(
        database_url,
        user_id=user_id,
        kinds=["gmail_full_reconcile"],
    )
    if reconcile_generation:
        if not active_reconciliations:
            enqueue_job(
                database_url,
                kind="gmail_full_reconcile",
                queue="slow",
                user_id=user_id,
                dedupe_key=f"gmail-full-reconcile:{user_id}:resume:{reconcile_generation}",
                priority=100,
                payload={"user_id": user_id, "batch_size": 250},
            )
    if (
        state is not None
        and sync_generation
        and not bool(getattr(state, "history_metadata_complete", False))
        and not reconcile_generation
        and not active_reconciliations
    ):
        enqueue_job(
            database_url,
            kind="gmail_full_reconcile",
            queue="slow",
            user_id=user_id,
            dedupe_key=f"gmail-full-reconcile:{user_id}:progressive-upgrade:{sync_generation}",
            priority=100,
            payload={"user_id": user_id, "batch_size": 100},
        )
        active_reconciliations = 1
    elif (
        state is not None
        and state.first_batch_imported_at
        and not gmail_history_cursor_is_authoritative(state)
        and not active_reconciliations
    ):
        enqueue_job(
            database_url,
            kind="gmail_full_reconcile",
            queue="slow",
            user_id=user_id,
            dedupe_key=f"gmail-full-reconcile:{user_id}:trigger",
            priority=100,
            payload={"user_id": user_id, "batch_size": 250},
        )
    if state is None or not state.first_batch_imported_at:
        enqueue_job(
            database_url,
            kind="gmail_import_batch",
            queue="critical",
            user_id=user_id,
            dedupe_key=f"first-run:{user_id}",
            priority=100,
            payload={"user_id": user_id, "batch_size": FIRST_BATCH_SIZE, "first_run": True},
        )
    elif _ai_grouping_enabled(settings) and not state.first_groups_ready_at:
        enqueue_job(
            database_url,
            kind="first_run_ai_grouping",
            queue="critical",
            user_id=user_id,
            dedupe_key=f"first-run-ai-grouping:{user_id}",
            priority=95,
            payload={"user_id": user_id, "batch_size": FIRST_BATCH_SIZE},
        )
    active_backfills = count_active_jobs(database_url, user_id=user_id, kinds=["gmail_backfill"])
    full_backfill_completed_at = getattr(state, "full_backfill_completed_at", None) if state is not None else None
    legacy_backfill_started = bool(
        state
        and (
            getattr(state, "full_backfill_started_at", None)
            or getattr(state, "full_backfill_cursor", None)
            or active_backfills
        )
    )
    full_backfill_needed = bool(
        state
        and state.first_batch_imported_at
        and not full_backfill_completed_at
        and legacy_backfill_started
    )
    if full_backfill_needed and not active_backfills:
        enqueue_job(
            database_url,
            kind="gmail_backfill",
            queue="slow",
            user_id=user_id,
            dedupe_key=f"gmail-backfill:{user_id}:resume",
            priority=80,
            payload={"user_id": user_id, "batch_size": BACKFILL_BATCH_SIZE},
        )
    body_backfill_needed = bool(
        state
        and sync_generation
        and getattr(state, "history_metadata_complete", False)
        and (
            not getattr(state, "history_body_complete", False)
            or not getattr(state, "attachment_descriptors_complete", True)
        )
    )
    if body_backfill_needed and not count_active_jobs(
        database_url,
        user_id=user_id,
        kinds=["gmail_body_backfill"],
    ):
        enqueue_job(
            database_url,
            kind="gmail_body_backfill",
            queue="slow",
            user_id=user_id,
            dedupe_key=f"gmail-body-backfill:{user_id}",
            priority=40,
            max_attempts=10,
            payload={"user_id": user_id, "batch_size": 25},
            wake_existing=False,
        )
    status_counts = count_mail_groups_by_enrichment_status(database_url, user_id=user_id)
    if _ai_grouping_enabled(settings) and status_counts.get("pending", 0) > 0:
        enqueue_job(
            database_url,
            kind="mail_group_enrich",
            queue="default",
            user_id=user_id,
            dedupe_key=f"mail-group-enrich:{user_id}",
            priority=40,
            payload={"user_id": user_id},
        )
        enqueue_projection_refresh(settings, user_id=user_id, priority=20)


def build_app_session_response(settings: Settings, *, user) -> AppSessionResponse:
    """Read the prepared app-session snapshot shared by dashboard and Gmail."""
    database_url = str(settings.database_path)
    auth = _google_auth_state(settings, user_id=user.id)
    if auth.connected:
        ensure_background_import_work(settings, user_id=user.id)
    snapshot = get_app_session_snapshot(database_url, user_id=user.id) if auth.connected else None
    if snapshot is None:
        return _empty_app_session_response(settings, user=user, auth=auth)
    if str(snapshot.sync.get("projection_version") or "") != APP_SESSION_PROJECTION_VERSION:
        refreshed = refresh_app_session_snapshot(settings, user_id=user.id)
        if refreshed is not None:
            return refreshed
    ai_grouping_enabled = _ai_grouping_enabled(settings)
    dashboard = DashboardResponse.model_validate(snapshot.dashboard)
    mailbox = MailboxResponse.model_validate(snapshot.mailbox)
    if not ai_grouping_enabled:
        dashboard = _without_legacy_ai_dashboard(dashboard)
        if _mailbox_contains_legacy_ai_content(mailbox):
            mailbox = build_mailbox_response(settings, user_id=user.id, label="inbox", limit=100)
    sync = AppSessionSyncState.model_validate(snapshot.sync)
    import_state = get_import_state(database_url, user_id=user.id)
    full_import_running, full_import_completed, full_import_completed_at = _full_import_status(
        import_state,
        active_backfill_jobs=count_active_jobs(
            database_url,
            user_id=user.id,
            kinds=["gmail_backfill"],
        ),
        active_reconciliation_jobs=count_active_jobs(
            database_url,
            user_id=user.id,
            kinds=["gmail_full_reconcile"],
        ),
    )
    mailbox = mailbox.model_copy(
        update={
            "full_import_running": full_import_running,
            "full_import_completed": full_import_completed,
            **_sync_progress_kwargs(import_state),
        }
    )
    status_counts = count_mail_groups_by_enrichment_status(database_url, user_id=user.id)
    last_ai_error = latest_mail_group_ai_error(database_url, user_id=user.id) if ai_grouping_enabled else None
    live_runtime = _mail_runtime_status(
        database_url,
        expected_release_sha=getattr(settings, "release_sha", "local"),
        status_counts=status_counts,
        last_ai_error=last_ai_error,
        ai_grouping_enabled=ai_grouping_enabled,
    )
    dashboard.runtime_status = {**dashboard.runtime_status, **live_runtime}
    legacy_ai_error = sync.last_ai_error
    sync = sync.model_copy(
        update={
            "enrichment_pending_count": status_counts.get("pending", 0) if ai_grouping_enabled else 0,
            "ready_group_count": status_counts.get("ready", 0) if ai_grouping_enabled else mailbox.total_threads,
            "last_ai_error": last_ai_error,
            "full_import_running": full_import_running,
            "full_import_completed": full_import_completed,
            "full_import_completed_at": full_import_completed_at,
            "last_error": (
                None
                if not ai_grouping_enabled and legacy_ai_error and sync.last_error == legacy_ai_error
                else sync.last_error or (last_ai_error if ai_grouping_enabled and status_counts.get("ready", 0) == 0 else None)
            ),
            **_sync_progress_kwargs(import_state),
        }
    )
    readiness = _readiness_from_snapshot(settings, user=user, dashboard=dashboard, mailbox=mailbox, sync=sync)
    return AppSessionResponse(
        user=AppSessionUser(
            id=user.id,
            email=user.email,
            first_name=_first_name(user.profile),
            display_name=user.profile.display_name,
        ),
        readiness=readiness,
        dashboard=dashboard,
        mailbox=mailbox,
        sync=sync,
    )


def _empty_app_session_response(settings: Settings, *, user, auth: GoogleAuthState) -> AppSessionResponse:
    database_url = str(settings.database_path)
    state = get_import_state(database_url, user_id=user.id) if auth.connected else None
    full_import_running, full_import_completed, full_import_completed_at = _full_import_status(
        state,
        active_backfill_jobs=(
            count_active_jobs(
                database_url,
                user_id=user.id,
                kinds=["gmail_backfill"],
            )
            if auth.connected
            else 0
        ),
        active_reconciliation_jobs=(
            count_active_jobs(
                database_url,
                user_id=user.id,
                kinds=["gmail_full_reconcile"],
            )
            if auth.connected
            else 0
        ),
    )
    dashboard = DashboardResponse(auth=auth, profile=user.profile, feed=FeedResponse())
    mailbox = MailboxResponse(
        label="inbox",
        total_threads=0,
        full_import_running=full_import_running,
        full_import_completed=full_import_completed,
        **_sync_progress_kwargs(state),
    )
    sync = AppSessionSyncState(
        last_sync_at=None,
        last_error=None,
        enrichment_pending_count=0,
        ready_group_count=0,
        oldest_imported_at=None,
        full_import_running=full_import_running,
        full_import_completed=full_import_completed,
        full_import_completed_at=full_import_completed_at,
        **_sync_progress_kwargs(state),
    )
    readiness = _readiness_from_snapshot(settings, user=user, dashboard=dashboard, mailbox=mailbox, sync=sync)
    return AppSessionResponse(
        user=AppSessionUser(
            id=user.id,
            email=user.email,
            first_name=_first_name(user.profile),
            display_name=user.profile.display_name,
        ),
        readiness=readiness,
        dashboard=dashboard,
        mailbox=mailbox,
        sync=sync,
    )


def _readiness_from_snapshot(
    settings: Settings,
    *,
    user,
    dashboard: DashboardResponse,
    mailbox: MailboxResponse,
    sync: AppSessionSyncState,
) -> PostLoginReadinessResponse:
    state = get_import_state(str(settings.database_path), user_id=user.id)
    ready_dashboard_count = len(dashboard.feed.now) + len(dashboard.feed.today) + len(dashboard.feed.worth_knowing)
    has_prior_product = bool((state and state.first_batch_imported_at) or mailbox.total_threads > 0)
    mode = "returning" if has_prior_product else "first_time"
    google_ready = dashboard.auth.connected and not dashboard.auth.reauth_required
    first_import_completed = bool(state and state.first_batch_imported_at)
    mailbox_ready = bool(google_ready and (mailbox.total_threads > 0 or first_import_completed))
    ai_grouping_enabled = _ai_grouping_enabled(settings)
    dashboard_ready = bool(ready_dashboard_count > 0) if ai_grouping_enabled else mailbox_ready
    ready_to_enter = bool(mailbox_ready and ((state and state.first_batch_imported_at) or mode == "returning"))
    stage = _post_login_stage(
        mode=mode,
        ready_to_enter=ready_to_enter,
        state=state,
        active_setup_jobs=0,
        mailbox_ready=mailbox_ready,
        dashboard_ready=dashboard_ready,
        ai_grouping_enabled=ai_grouping_enabled,
    )
    display_name = _first_name(user.profile) or user.profile.display_name or user.email
    return PostLoginReadinessResponse(
        mode=mode,  # type: ignore[arg-type]
        stage=stage,  # type: ignore[arg-type]
        ready_to_enter=ready_to_enter,
        dashboard_ready=dashboard_ready,
        mailbox_ready=mailbox_ready,
        ready_dashboard_count=ready_dashboard_count,
        ready_mail_group_count=sync.ready_group_count,
        full_import_running=sync.full_import_running,
        full_import_completed=sync.full_import_completed,
        user_display_name=display_name,
        error_message=sync.last_error if sync.last_error and not ready_to_enter else None,
        **_sync_progress_kwargs(state),
    )


def refresh_app_session_snapshot(settings: Settings, *, user_id: str) -> AppSessionResponse | None:
    from app.db.repository import get_user
    from app.services.auth import CurrentUser

    stored = get_user(str(settings.database_path), user_id)
    if stored is None:
        return None
    user = CurrentUser(id=stored.id, email=stored.email, display_name=stored.display_name)
    database_url = str(settings.database_path)
    auth = _google_auth_state(settings, user_id=user.id)
    dashboard = build_dashboard_response(settings, user_id=user.id, auth=auth, profile=user.profile)
    mailbox = build_mailbox_response(settings, user_id=user.id, label="inbox", limit=100)
    # Progressive bootstrap owns body priority: initial 25 at 90, remaining
    # initial window at 60, and history via one resumable slow job. Merely
    # building an app-session snapshot must not create a second Inbox-shaped
    # warm-up queue that changes those global Gmail priorities.
    ai_grouping_enabled = _ai_grouping_enabled(settings)
    status_counts = count_mail_groups_by_enrichment_status(database_url, user_id=user.id)
    last_ai_error = latest_mail_group_ai_error(database_url, user_id=user.id) if ai_grouping_enabled else None
    state = get_import_state(database_url, user_id=user.id)
    sync = AppSessionSyncState(
        last_sync_at=state.last_import_completed_at if state else None,
        last_error=(state.last_sync_error if state else None) or (last_ai_error if ai_grouping_enabled and status_counts.get("ready", 0) == 0 else None),
        enrichment_pending_count=status_counts.get("pending", 0) if ai_grouping_enabled else 0,
        ready_group_count=status_counts.get("ready", 0) if ai_grouping_enabled else mailbox.total_threads,
        oldest_imported_at=oldest_imported_message_at(database_url, user_id=user.id),
        full_import_running=mailbox.full_import_running,
        full_import_completed=mailbox.full_import_completed,
        full_import_completed_at=(
            getattr(state, "full_backfill_completed_at", None)
            if mailbox.full_import_completed
            else None
        ),
        pending_action_count=count_pending_thread_actions(database_url, user_id=user.id),
        last_ai_error=last_ai_error,
        **_sync_progress_kwargs(state),
    )
    upsert_app_session_snapshot(
        database_url,
        user_id=user.id,
        dashboard=dashboard.model_dump(mode="json"),
        mailbox=mailbox.model_dump(mode="json"),
        sync={**sync.model_dump(mode="json"), "projection_version": APP_SESSION_PROJECTION_VERSION},
    )
    return build_app_session_response(settings, user=user)


def refresh_visible_mail_projection(settings: Settings, *, user_id: str) -> int:
    # Canonical raw Gmail threads are read directly from gmail_messages in this
    # release. The visible-group projection only serves the retired AI/grouped
    # product and rebuilding it wastes a full legacy scan after every mutation.
    if not _ai_grouping_enabled(settings):
        return 0
    database_url = str(settings.database_path)
    _refresh_ai_lifecycle_groups(settings, user_id=user_id)
    groups = list_mail_groups(database_url, user_id=user_id, limit=MAILBOX_REBUILD_LIMIT, include_pending=False)
    group_ids = [group.id for group in groups]
    group_messages = list_messages_for_groups(database_url, user_id=user_id, group_ids=group_ids)
    result = build_inbox_projection(groups=groups, group_messages=group_messages)
    replace_visible_mail_projection(
        database_url,
        user_id=user_id,
        visibility="inbox",
        groups=result.groups,
        audit_events=result.audit_events,
    )
    return len(result.groups)


def _refresh_ai_lifecycle_groups(settings: Settings, *, user_id: str) -> int:
    """Compatibility tombstone for historical lifecycle projection callers."""
    del settings, user_id
    return 0


def enqueue_projection_refresh(settings: Settings, *, user_id: str, priority: int = 15) -> str:
    if not _ai_grouping_enabled(settings):
        return ""
    job = enqueue_job(
        str(settings.database_path),
        kind="projection_refresh",
        queue="default",
        user_id=user_id,
        dedupe_key=f"projection-refresh:{user_id}",
        priority=priority,
        payload={"user_id": user_id},
    )
    return job.id


def _mail_runtime_status(
    database_url: str,
    *,
    expected_release_sha: str,
    status_counts: dict[str, int],
    last_ai_error: str | None,
    ai_grouping_enabled: bool = False,
) -> dict[str, Any]:
    queue_health = get_queue_health(database_url, expected_release_sha=expected_release_sha)
    ready_count = status_counts.get("ready", 0)
    pending_count = status_counts.get("pending", 0)
    failed_count = status_counts.get("failed", 0)
    runtime_status = {
        "canonical_ready": bool(ready_count),
        "pending_group_count": pending_count,
        "ready_group_count": ready_count,
        "failed_group_count": failed_count,
        "worker_online": queue_health.worker_online,
        "required_queues_ready": queue_health.required_queues_ready,
        "queue_depth": queue_health.queue_depth,
    }
    if ai_grouping_enabled:
        runtime_status.update(
            {
                "ai_groups_ready": bool(ready_count),
                "last_ai_error": last_ai_error,
            }
        )
    return runtime_status


def build_post_login_readiness_response(
    settings: Settings,
    *,
    user,
    dashboard: DashboardResponse | None = None,
    mailbox: MailboxResponse | None = None,
) -> PostLoginReadinessResponse:
    database_url = str(settings.database_path)
    state = get_import_state(database_url, user_id=user.id)
    dashboard_since = (datetime.now(timezone.utc) - timedelta(days=DASHBOARD_DAYS)).isoformat()
    ready_mail_group_count = count_mail_groups(database_url, user_id=user.id)
    mailbox_thread_count = mailbox.total_threads if mailbox is not None else count_mailbox_threads(database_url, user_id=user.id, label="all")
    ready_dashboard_count = (
        sum(len(section) for section in [dashboard.feed.now, dashboard.feed.today, dashboard.feed.worth_knowing])
        if dashboard is not None
        else count_dashboard_mail_groups(database_url, user_id=user.id, since_iso=dashboard_since)
    )
    active_backfill_jobs = count_active_jobs(database_url, user_id=user.id, kinds=["gmail_backfill"])
    active_reconciliation_jobs = count_active_jobs(
        database_url,
        user_id=user.id,
        kinds=["gmail_full_reconcile"],
    )
    active_setup_jobs = count_active_jobs(
        database_url,
        user_id=user.id,
        kinds=["gmail_import_batch", *([] if not _ai_grouping_enabled(settings) else ["first_run_ai_grouping", "mail_group_enrich"])],
    )
    full_import_running, full_import_completed, _full_import_completed_at = _full_import_status(
        state,
        active_backfill_jobs=active_backfill_jobs,
        active_reconciliation_jobs=active_reconciliation_jobs,
    )
    has_prior_product = bool((state and state.first_batch_imported_at) or mailbox_thread_count > 0)
    mode = "returning" if has_prior_product else "first_time"
    auth = _google_auth_state(settings, user_id=user.id)
    google_ready = auth.connected and not auth.reauth_required
    first_import_completed = bool(state and state.first_batch_imported_at)
    mailbox_ready = bool(google_ready and (mailbox_thread_count > 0 or first_import_completed))
    ai_grouping_enabled = _ai_grouping_enabled(settings)
    dashboard_ready = bool(ready_dashboard_count > 0) if ai_grouping_enabled else mailbox_ready
    ready_to_enter = bool(mailbox_ready and ((state and state.first_batch_imported_at) or mode == "returning"))
    stage = _post_login_stage(
        mode=mode,
        ready_to_enter=ready_to_enter,
        state=state,
        active_setup_jobs=active_setup_jobs,
        mailbox_ready=mailbox_ready,
        dashboard_ready=dashboard_ready,
        ai_grouping_enabled=ai_grouping_enabled,
    )
    display_name = _first_name(user.profile) or user.profile.display_name or user.email
    return PostLoginReadinessResponse(
        mode=mode,  # type: ignore[arg-type]
        stage=stage,  # type: ignore[arg-type]
        ready_to_enter=ready_to_enter,
        dashboard_ready=dashboard_ready,
        mailbox_ready=mailbox_ready,
        ready_dashboard_count=ready_dashboard_count,
        ready_mail_group_count=ready_mail_group_count if ai_grouping_enabled else mailbox_thread_count,
        full_import_running=full_import_running,
        full_import_completed=full_import_completed,
        user_display_name=display_name,
        error_message=state.last_sync_error if state and state.last_sync_error and not ready_to_enter else None,
        **_sync_progress_kwargs(state),
    )


def _post_login_stage(
    *,
    mode: str,
    ready_to_enter: bool,
    state,
    active_setup_jobs: int,
    mailbox_ready: bool,
    dashboard_ready: bool,
    ai_grouping_enabled: bool,
) -> str:
    if ready_to_enter:
        return "welcome_back" if mode == "returning" else "ready"
    if state is not None and state.last_sync_error and not active_setup_jobs:
        return "failed"
    if state is None:
        return "starting_full_import"
    if not state.first_batch_imported_at:
        return "importing_recent_gmail"
    if not mailbox_ready:
        return "importing_recent_gmail"
    if not ai_grouping_enabled:
        return "ready"
    if not state.first_groups_ready_at:
        return "writing_titles"
    if mode == "first_time" and not state.first_dashboard_ready_at:
        return "building_dashboard"
    if not dashboard_ready:
        return "building_dashboard"
    return "ready"


def rebuild_mail_groups(
    settings: Settings,
    *,
    user_id: str,
    limit: int = 500,
    use_ai: bool = False,
    max_ai_groups: int | None = None,
) -> int:
    # Keep legacy keyword arguments source-compatible, but deterministic grouping
    # is the only implementation in the no-AI release.
    del use_ai, max_ai_groups
    if not _ai_grouping_enabled(settings):
        return 0
    database_url = str(settings.database_path)
    if not user_can_write_gmail(database_url, user_id=user_id):
        return 0
    messages = list_recent_messages(database_url, user_id=user_id, limit=limit)
    candidates = _candidate_groups(messages)
    touched = 0
    for group_key, grouped_messages in candidates.items():
        latest = max(grouped_messages, key=lambda item: item.internal_date or item.updated_at)
        group_hash = _group_hash(grouped_messages)
        existing = get_mail_group_by_key(database_url, user_id=user_id, group_key=group_key)
        if existing is not None and existing.generated_from_hash == group_hash and existing.generated_at is not None:
            enrichment = _enrichment_from_existing(existing)
            enrichment["_ai_ready"] = existing.enrichment_status == "ready"
            generated_at = existing.generated_at
        else:
            enrichment = enrich_group(
                settings,
                messages=grouped_messages,
                group_key=group_key,
                generated_from_hash=group_hash,
                use_ai=False,
            )
            generated_at = datetime.now(timezone.utc).isoformat() if enrichment.get("_ai_ready") else None
        ai_ready = bool(enrichment.get("_ai_ready"))
        group = upsert_mail_group(
            database_url,
            user_id=user_id,
            group_key=group_key,
            group_type=enrichment["group_type"],
            ai_title=enrichment["ai_title"],
            ai_summary=enrichment["ai_summary"],
            labels=enrichment["labels"],
            action_needed=bool(enrichment["action_needed"]),
            action_type=str(enrichment["action_type"]),
            priority=int(enrichment["priority"]),
            timing_band=str(enrichment["timing_band"]),
            dashboard_visible=bool(enrichment["dashboard_visible"]) if ai_ready else False,
            latest_message_at=latest.internal_date or latest.updated_at,
            latest_message_id=latest.message_id,
            generated_from_hash=group_hash,
            generated_at=generated_at,
            enrichment_status="ready" if ai_ready else "pending",
            membership_source=_member_reason(grouped_messages[0]),
            ai_model=existing.ai_model if ai_ready and existing is not None else None,
            ai_error=str(enrichment.get("_ai_error"))[:1000] if enrichment.get("_ai_error") else None,
            ai_generated_at=generated_at,
            **_classification_kwargs(enrichment, generated_at=generated_at),
        )
        replace_group_members(
            database_url,
            user_id=user_id,
            group_id=group.id,
            members=[(message, _member_reason(message), 0.9) for message in grouped_messages],
        )
        if ai_ready:
            _persist_message_ai_titles(database_url, user_id=user_id, enrichment=enrichment, messages=grouped_messages, generated_at=generated_at)
        touched += 1
    return touched


def rebuild_touched_mail_groups(
    settings: Settings,
    *,
    user_id: str,
    message_ids: list[str],
    use_ai: bool = False,
    max_ai_groups: int | None = None,
    fallback_ready: bool = False,
) -> int:
    # Keep legacy keyword arguments source-compatible, but deterministic grouping
    # is the only implementation in the no-AI release.
    del use_ai, max_ai_groups
    if not _ai_grouping_enabled(settings):
        return 0
    database_url = str(settings.database_path)
    if not user_can_write_gmail(database_url, user_id=user_id):
        return 0
    messages = list_messages_by_ids(database_url, user_id=user_id, message_ids=message_ids)
    candidates = _candidate_groups(messages)
    touched = 0
    for group_key, grouped_messages in candidates.items():
        existing = get_mail_group_by_key(database_url, user_id=user_id, group_key=group_key)
        candidate_messages = _merge_group_messages(
            [*(list_group_messages(database_url, user_id=user_id, group_id=existing.id) if existing is not None else []), *grouped_messages]
        )
        latest = max(candidate_messages, key=lambda item: item.internal_date or item.updated_at)
        group_hash = _group_hash(candidate_messages)
        if (
            existing is not None
            and existing.enrichment_status == "ready"
            and existing.generated_from_hash == group_hash
            and existing.generated_at is not None
        ):
            enrichment = _enrichment_from_existing(existing)
            enrichment["_ai_ready"] = True
            generated_at = existing.generated_at
            membership_source = existing.membership_source
            ai_model = existing.ai_model
            ai_error = existing.ai_error
            ai_generated_at = existing.ai_generated_at
        else:
            enrichment = enrich_group(
                settings,
                messages=candidate_messages,
                group_key=group_key,
                generated_from_hash=group_hash,
                use_ai=False,
            )
            generated_at = datetime.now(timezone.utc).isoformat() if enrichment.get("_ai_ready") else None
            membership_source = _member_reason(grouped_messages[0])
            ai_model = None
            ai_error = str(enrichment.get("_ai_error"))[:1000] if enrichment.get("_ai_error") else None
            ai_generated_at = generated_at

        ai_ready = bool(enrichment.get("_ai_ready"))
        generated_at = generated_at or (datetime.now(timezone.utc).isoformat() if fallback_ready else None)
        group = upsert_mail_group(
            database_url,
            user_id=user_id,
            group_key=group_key,
            group_type=enrichment["group_type"],
            ai_title=enrichment["ai_title"],
            ai_summary=enrichment["ai_summary"],
            labels=enrichment["labels"],
            action_needed=bool(enrichment["action_needed"]),
            action_type=str(enrichment["action_type"]),
            priority=int(enrichment["priority"]),
            timing_band=str(enrichment["timing_band"]),
            dashboard_visible=bool(enrichment["dashboard_visible"]) if ai_ready else False,
            latest_message_at=latest.internal_date or latest.updated_at,
            latest_message_id=latest.message_id,
            generated_from_hash=group_hash,
            generated_at=generated_at,
            enrichment_status="ready" if ai_ready else "pending",
            membership_source=membership_source,
            ai_model=ai_model,
            ai_error=ai_error,
            ai_generated_at=ai_generated_at,
            **_classification_kwargs(enrichment, generated_at=generated_at),
        )
        replace_group_members(
            database_url,
            user_id=user_id,
            group_id=group.id,
            members=[(message, _member_reason(message), 0.9) for message in candidate_messages],
        )
        if ai_ready:
            _persist_message_ai_titles(database_url, user_id=user_id, enrichment=enrichment, messages=candidate_messages, generated_at=generated_at)
        touched += 1
    return touched


def enrich_pending_mail_groups(
    settings: Settings,
    *,
    user_id: str,
    limit: int = MAIL_GROUP_ENRICH_BATCH_SIZE,
    preferred_group_ids: list[str] | None = None,
) -> int:
    """Compatibility tombstone for historical enrichment jobs."""
    del settings, user_id, limit, preferred_group_ids
    return 0


def run_first_run_ai_grouping(settings: Settings, *, user_id: str, limit: int = FIRST_BATCH_SIZE) -> int:
    """Compatibility tombstone for historical first-run grouping jobs."""
    del settings, user_id, limit
    return 0


def build_dashboard_response(
    settings: Settings,
    *,
    user_id: str | None,
    auth: GoogleAuthState,
    profile: DashboardProfile | None = None,
    debug_classification: bool = False,
) -> DashboardResponse:
    if user_id is None or not auth.connected:
        return DashboardResponse(auth=auth, feed=FeedResponse())
    since = (datetime.now(timezone.utc) - timedelta(days=DASHBOARD_DAYS)).isoformat()
    database_url = str(settings.database_path)
    ai_grouping_enabled = _ai_grouping_enabled(settings)
    groups = list_dashboard_mail_groups(database_url, user_id=user_id, since_iso=since) if ai_grouping_enabled else []
    manual_tasks = list_open_manual_tasks(database_url, user_id=user_id)
    outcomes = get_latest_entity_outcomes(
        database_url,
        user_id=user_id,
        entity_ids=[*[group.id for group in groups], *[task.entity_id for task in manual_tasks]],
    )
    groups = [group for group in groups if not _outcome_suppresses(outcomes.get(group.id))]
    manual_tasks = [task for task in manual_tasks if not _outcome_suppresses(outcomes.get(task.entity_id))]
    group_messages = list_messages_for_groups(database_url, user_id=user_id, group_ids=[group.id for group in groups])
    groups = [group for group in groups if _group_has_dashboard_visible_mail(group_messages.get(group.id, []))]
    groups = _dedupe_groups_by_messages(groups, group_messages)
    groups = _collapse_dashboard_workflow_groups(groups, group_messages)
    status_counts = count_mail_groups_by_enrichment_status(database_url, user_id=user_id)
    last_ai_error = latest_mail_group_ai_error(database_url, user_id=user_id) if ai_grouping_enabled else None
    feed = FeedResponse()
    for group in groups:
        _append_feed_item(feed, _attention_item_from_group(group))
    for task in manual_tasks:
        _append_feed_item(feed, _attention_item_from_manual_task(task))
    briefing = _dashboard_briefing(profile=profile, feed=feed)
    runtime_status = {
        "feed_source": "mail_groups" if groups else "manual_tasks" if manual_tasks else "empty",
        "last_mailbox_sync_at": None,
        "queue_lag_seconds": None,
        "stale_reason": None,
        **_mail_runtime_status(
            database_url,
            expected_release_sha=getattr(settings, "release_sha", "local"),
            status_counts=status_counts,
            last_ai_error=last_ai_error,
            ai_grouping_enabled=ai_grouping_enabled,
        ),
    }
    if debug_classification:
        runtime_status["classification_debug"] = [
            {
                "group_id": group.id,
                "title": group.ai_title,
                "classification_version": group.classification_version,
                "classification": group.classification or {},
                "confidence": group.classification_confidence,
                "ranking_reason": group.ranking_reason,
                "suppression_reason": group.suppression_reason,
                "timing_band": group.timing_band,
                "priority": group.priority,
                "dashboard_visible": group.dashboard_visible,
            }
            for group in groups
        ]
    dashboard = DashboardResponse(
        auth=auth,
        profile=profile,
        briefing=briefing,
        feed=feed,
        runtime_status=runtime_status,
    )
    return dashboard if ai_grouping_enabled else _without_legacy_ai_dashboard(dashboard)


def _recent_visible_days(settings: Settings) -> int:
    return max(1, int(getattr(settings, "gmail_recent_days", RECENT_VISIBLE_DAYS) or RECENT_VISIBLE_DAYS))


def _dashboard_briefing(*, profile: DashboardProfile | None, feed: FeedResponse) -> DashboardBriefing:
    items = [*feed.now, *feed.today, *feed.worth_knowing]
    calendar_items = [item for item in items if item.source == "calendar"]
    reply_items = [item for item in items if item.source == "gmail" and item.primary_action == "reply"]
    important_item = _important_briefing_item(items)
    task_items = [
        item
        for item in items
        if item.source in {"gmail", "manual"}
        and item.need_type == "decision"
        and item.primary_action != "reply"
        and (important_item is None or item.entity_id != important_item["mail_group_id"])
    ]
    name = _first_name(profile)
    greeting = f"{_time_greeting()}, {name}." if name else f"{_time_greeting()}."
    parts = [
        {
            "type": "meetings",
            "emoji": "📆",
            "count": len(calendar_items),
            "text": "meetings",
        },
        {
            "type": "tasks",
            "emoji": "✅",
            "count": len(task_items),
            "text": "tasks",
        },
        {
            "type": "emails",
            "emoji": "📨",
            "count": len(reply_items),
            "text": "emails to reply",
        },
    ]
    calendar_availability = _calendar_availability(calendar_items)
    brief_segments = [_part_sentence(part) for part in parts]
    brief = f"You have {', '.join(brief_segments[:-1])}"
    if len(brief_segments) > 1:
        brief += f" and {brief_segments[-1]}."
    elif brief_segments:
        brief += f"{brief_segments[0]}."
    else:
        brief += "nothing urgent right now."
    if important_item is not None:
        brief += f" One important thing: {important_item['emoji']} {important_item['text']}."
    if calendar_availability is not None:
        brief += f" You are {calendar_availability['text']}."
    return DashboardBriefing(
        headline=greeting,
        brief=brief,
        parts=parts,
        important=important_item,
        calendar_availability=calendar_availability,
    )


def _important_briefing_item(items: list[AttentionItem]) -> dict[str, Any] | None:
    candidates = [
        item
        for item in items
        if item.source == "gmail" and item.need_type == "decision" and item.primary_action in {"pay", "confirm", "review", "open"}
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (_importance_score(item), item.created_at), reverse=True)
    item = candidates[0]
    return {
        "emoji": _important_emoji(item.primary_action),
        "count": 1,
        "text": _important_text(item),
        "mail_group_id": item.entity_id,
        "action_type": item.primary_action,
    }


def _importance_score(item: AttentionItem) -> int:
    score = {"high": 3, "medium": 2, "low": 1}.get(item.importance_level or "low", 1)
    if item.primary_action == "pay":
        score += 5
    elif item.primary_action == "confirm":
        score += 4
    elif item.timing_band == "now":
        score += 3
    elif item.timing_band == "today":
        score += 2
    return score


def _important_emoji(action: str) -> str:
    if action == "pay":
        return "💸"
    if action == "confirm":
        return "📌"
    if action == "review":
        return "👀"
    return "⚡"


def _important_text(item: AttentionItem) -> str:
    title = item.title.strip()
    if item.primary_action == "pay":
        return f"{title} payment due today" if "due" not in title.lower() else title
    if item.primary_action == "confirm":
        return f"{title} needs confirmation"
    return title


def _calendar_availability(calendar_items: list[AttentionItem]) -> dict[str, Any] | None:
    timed_items = [item for item in calendar_items if item.due_at]
    if not timed_items:
        return {
            "emoji": "🌄",
            "kind": "no_meetings",
            "time": None,
            "text": "clear on calendar today",
        }
    latest = max(timed_items, key=lambda item: item.due_at or "")
    time_label = _briefing_time_label(latest.due_at)
    if time_label is None:
        return None
    return {
        "emoji": "🌄",
        "kind": "mostly_free_after",
        "time": time_label,
        "text": f"mostly free after {time_label}",
    }


def _briefing_time_label(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    local = parsed.astimezone()
    hour = local.hour
    suffix = "am" if hour < 12 else "pm"
    normalized = hour % 12 or 12
    return f"{normalized} {suffix}"


def _part_sentence(part: dict[str, Any]) -> str:
    count = int(part.get("count") or 0)
    text = str(part.get("text") or "")
    emoji = str(part.get("emoji") or "")
    if count == 0:
        return f"{emoji} no {text}"
    if count == 1 and text.endswith("s"):
        text = text[:-1]
    return f"{emoji} {count} {text}"


def _time_greeting() -> str:
    hour = datetime.now().astimezone().hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def _first_name(profile: DashboardProfile | None) -> str | None:
    if profile is None:
        return None
    source = profile.display_name
    if not source:
        return None
    if "@" in source:
        return None
    name = source.replace(".", " ").replace("_", " ").replace("-", " ").strip()
    return name.split()[0].title() if name else None


def build_mailbox_response(
    settings: Settings,
    *,
    user_id: str,
    label: str = "inbox",
    limit: int = 150,
    cursor: str | None = None,
    search_query: str | None = None,
) -> MailboxResponse:
    database_url = str(settings.database_path)
    ensure_background_import_work(settings, user_id=user_id)
    state = get_import_state(database_url, user_id=user_id)
    active_backfill_jobs = count_active_jobs(database_url, user_id=user_id, kinds=["gmail_backfill"])
    active_reconciliation_jobs = count_active_jobs(
        database_url,
        user_id=user_id,
        kinds=["gmail_full_reconcile"],
    )
    full_import_running, full_import_completed, _full_import_completed_at = _full_import_status(
        state,
        active_backfill_jobs=active_backfill_jobs,
        active_reconciliation_jobs=active_reconciliation_jobs,
    )
    status_counts = count_mail_groups_by_enrichment_status(database_url, user_id=user_id)
    mailbox_label = _mailbox_label(label)
    page = list_mailbox_thread_page(
        database_url,
        user_id=user_id,
        label=mailbox_label,
        limit=limit,
        cursor=cursor,
        since_iso=None,
        search_query=search_query,
    )
    threads = page.threads
    initial_window_positions = (
        list_gmail_initial_window_positions(
            database_url,
            user_id=user_id,
            gmail_thread_ids=[thread_id for thread_id, _ in threads],
        )
        if state is not None and getattr(state, "sync_generation", None)
        else {}
    )
    thread_ai_groups = (
        list_mail_groups_for_gmail_threads(
            database_url,
            user_id=user_id,
            gmail_thread_ids=[thread_id for thread_id, _ in threads],
        )
        if _ai_grouping_enabled(settings)
        else {}
    )
    projected_groups = (
        list_visible_groups_for_gmail_threads(
            database_url,
            user_id=user_id,
            visibility="inbox",
            gmail_thread_ids=[thread_id for thread_id, _ in threads],
        )
        if _ai_grouping_enabled(settings) and mailbox_label in {"inbox", "all"}
        else {}
    )
    projected_group_ids = list(dict.fromkeys(group.id for group in projected_groups.values()))
    projected_group_messages = (
        list_messages_for_visible_groups(database_url, user_id=user_id, visible_group_ids=projected_group_ids)
        if projected_group_ids
        else {}
    )
    rows: list[GmailThreadRow] = []
    entries: list[MailboxDisplayClusterEntry] = []
    rendered_thread_ids: set[str] = set()
    for thread_id, messages in threads:
        if not messages or thread_id in rendered_thread_ids:
            continue
        projected_group = projected_groups.get(thread_id)
        if projected_group is not None:
            grouped_messages = projected_group_messages.get(projected_group.id, [])
            if _visible_group_has_mailbox_messages(grouped_messages, mailbox_label):
                rendered_thread_ids.update(
                    message.gmail_thread_id or message.message_id
                    for message in grouped_messages
                    if message.gmail_thread_id or message.message_id
                )
                rows.append(_gmail_row_from_visible_group(projected_group, grouped_messages, mailbox_label))
                continue
        rendered_thread_ids.add(thread_id)
        row = _gmail_row_from_canonical_thread(thread_id, messages, mailbox_label, thread_ai_groups.get(thread_id))
        if thread_id in initial_window_positions:
            row = row.model_copy(
                update={"initial_window_position": initial_window_positions[thread_id]}
            )
        if _ai_grouping_enabled(settings):
            entries.append(
                MailboxDisplayClusterEntry(
                    thread_id=thread_id,
                    messages=messages,
                    row=row,
                    cluster_key=_mailbox_display_cluster_key(thread_id=thread_id, messages=messages, mailbox_label=mailbox_label),
                )
            )
        else:
            rows.append(row)
    if _ai_grouping_enabled(settings):
        rows.extend(_mailbox_display_cluster_rows(entries, mailbox_label=mailbox_label))
    if page.order_source != "gmail":
        rows = _sort_mailbox_rows(rows)
    if _ai_grouping_enabled(settings):
        _enqueue_visible_enrichment(settings, user_id=user_id, rows=rows)
    return MailboxResponse(
        label=mailbox_label,
        total_threads=count_mailbox_threads(
            database_url,
            user_id=user_id,
            label=mailbox_label,
            since_iso=None,
            search_query=search_query,
        ),
        unread_threads=count_mailbox_threads(
            database_url,
            user_id=user_id,
            label=mailbox_label,
            since_iso=None,
            search_query=search_query,
            unread_only=True,
        ),
        next_cursor=page.next_cursor,
        loaded_threads=page.loaded_threads,
        window_days=None,
        sections=_bucket_rows_in_order(rows) if page.order_source == "gmail" else _bucket_rows(rows),
        ready_count=status_counts.get("ready", 0),
        pending_count=status_counts.get("pending", 0),
        mailbox_revision=latest_gmail_mailbox_revision(database_url, user_id=user_id),
        generated_at=datetime.now(timezone.utc).isoformat(),
        oldest_imported_at=oldest_imported_message_at(database_url, user_id=user_id),
        full_import_running=full_import_running,
        full_import_completed=full_import_completed,
        **_sync_progress_kwargs(state),
    )


def _enqueue_visible_enrichment(settings: Settings, *, user_id: str, rows: list[GmailThreadRow]) -> None:
    preferred_group_ids = [
        row.ai_group_id
        for row in rows[:100]
        if row.ai_group_id
        and (
            row.presentation_status == "ai_pending"
            or any(child.ai_title is None for child in row.children)
        )
    ]
    if not preferred_group_ids:
        return
    try:
        enqueue_job(
            str(settings.database_path),
            kind="mail_group_enrich",
            queue="default",
            user_id=user_id,
            dedupe_key=f"visible-mail-group-enrich:{user_id}",
            priority=75,
            payload={"user_id": user_id, "preferred_group_ids": list(dict.fromkeys(preferred_group_ids))[:MAIL_GROUP_ENRICH_BATCH_SIZE]},
        )
    except Exception as exc:  # pragma: no cover - background work should not break mailbox reads.
        logger.warning("Failed to enqueue visible mail-group enrichment for user %s error=%s", user_id, type(exc).__name__)


def build_mailbox_sync_state(settings: Settings, *, user_id: str) -> MailboxSyncStateResponse:
    database_url = str(settings.database_path)
    state = get_import_state(database_url, user_id=user_id)
    credential_error: str | None = None
    connected = False
    if user_can_write_gmail(database_url, user_id=user_id):
        credential_status = check_user_google_credentials(settings, user_id=user_id, refresh_expired=True)
        connected = credential_status.connected
        if not credential_status.connected and credential_status.has_stored_tokens:
            credential_error = credential_status.error
    if connected:
        ensure_background_import_work(settings, user_id=user_id)
    queue_health = get_queue_health(
        database_url, expected_release_sha=getattr(settings, "release_sha", "local")
    )
    poller_workers = [
        worker
        for worker in queue_health.workers
        if worker.get("fresh")
        and worker.get("release_matches_expected")
        and "gmail_poll" in {str(queue) for queue in worker.get("queues", [])}
    ]
    watch_error = getattr(state, "gmail_watch_error", None) if state else None
    watch_expiration = getattr(state, "gmail_watch_expiration_at", None) if state else None
    watch_started = getattr(state, "gmail_watch_started_at", None) if state else None
    if not settings.gmail_pubsub_topic:
        watch_status = "not_configured"
    elif watch_error:
        watch_status = "error"
    elif watch_expiration:
        watch_status = "active"
    elif watch_started:
        watch_status = "starting"
    else:
        watch_status = "not_started"
    last_sync_error = (
        credential_error
        or ((state.last_sync_error or getattr(state, "gmail_watch_error", None)) if state else None)
    )
    active_backfill_jobs = count_active_jobs(
        database_url,
        user_id=user_id,
        kinds=["gmail_backfill"],
    )
    active_reconciliation_jobs = count_active_jobs(
        database_url,
        user_id=user_id,
        kinds=["gmail_full_reconcile"],
    )
    full_import_running, full_import_completed, full_import_completed_at = _full_import_status(
        state,
        active_backfill_jobs=active_backfill_jobs,
        active_reconciliation_jobs=active_reconciliation_jobs,
    )
    return MailboxSyncStateResponse(
        connected=connected,
        last_history_id=(
            state.last_history_id
            if gmail_history_cursor_is_authoritative(state)
            else None
        ),
        last_full_sync_at=state.last_import_completed_at if state else None,
        watch_expiration_at=watch_expiration,
        last_sync_started_at=state.last_import_started_at if state else None,
        last_sync_completed_at=state.last_import_completed_at if state else None,
        watch_status=watch_status,
        last_delta_sync_at=getattr(state, "last_delta_sync_at", None) if state else None,
        last_poll_at=str(poller_workers[0].get("last_seen_at")) if poller_workers else None,
        poller_online=bool(poller_workers),
        mailbox_revision=latest_gmail_mailbox_revision(database_url, user_id=user_id),
        last_sync_error=last_sync_error,
        total_threads=count_mailbox_threads(database_url, user_id=user_id, label="all"),
        full_import_running=full_import_running,
        full_import_completed=full_import_completed,
        full_import_completed_at=full_import_completed_at,
        pending_action_count=count_pending_thread_actions(database_url, user_id=user_id),
        last_ai_error=None,
        **_sync_progress_kwargs(state),
    )


def build_mailbox_realtime_state(settings: Settings, *, user_id: str) -> MailboxRealtimeStateResponse:
    sync_state = build_mailbox_sync_state(settings, user_id=user_id)
    last_event = latest_event(settings, user_id=user_id)
    last_pubsub = latest_event(settings, user_id=user_id, event_type=GMAIL_PUBSUB_RECEIVED)
    pubsub_payload = last_pubsub.payload if last_pubsub is not None else {}
    return MailboxRealtimeStateResponse(
        watch_status=sync_state.watch_status,
        watch_expiration_at=sync_state.watch_expiration_at,
        last_history_id=sync_state.last_history_id,
        last_pubsub_received_at=last_pubsub.created_at if last_pubsub is not None else None,
        last_pubsub_history_id=str(pubsub_payload.get("history_id") or "") or None,
        last_delta_sync_at=sync_state.last_delta_sync_at,
        last_mailbox_event_id=last_event.id if last_event is not None else None,
        last_mailbox_event_at=last_event.created_at if last_event is not None else None,
        last_mailbox_event_type=last_event.event_type if last_event is not None else None,
        mailbox_revision=sync_state.mailbox_revision,
        poller_online=sync_state.poller_online,
        total_threads=sync_state.total_threads,
        last_sync_error=sync_state.last_sync_error,
    )


def build_group_detail_response(
    settings: Settings,
    *,
    user_id: str,
    group_id: str,
    limit: int = 50,
    offset: int = 0,
    promote_body_fetch: bool = True,
    maximum_snapshot_messages: int | None = None,
    maximum_snapshot_stored_bytes: int | None = None,
) -> ThreadReaderResponse | None:
    database_url = str(settings.database_path)
    if _is_mailbox_display_cluster_id(group_id):
        if not _ai_grouping_enabled(settings):
            return None
        return _build_mailbox_display_cluster_detail_response(
            settings,
            user_id=user_id,
            cluster_id=group_id,
            limit=limit,
            offset=offset,
            promote_body_fetch=promote_body_fetch,
        )

    # The public single-thread reader is deliberately paginated in Postgres.
    # Large conversations are a valid Gmail shape (mailing lists can have tens
    # of thousands of messages), so slicing a fully materialized Python list
    # here would turn the native client's bounded fallback into an unbounded
    # API read. Batch snapshots retain the all-or-nothing bounded path below.
    if maximum_snapshot_messages is None and maximum_snapshot_stored_bytes is None:
        page = get_gmail_thread_message_page(
            database_url,
            user_id=user_id,
            gmail_thread_id=group_id,
            limit=limit,
            offset=offset,
        )
        if page is not None:
            ai_group = (
                list_mail_groups_for_gmail_threads(database_url, user_id=user_id, gmail_thread_ids=[group_id]).get(group_id)
                if _ai_grouping_enabled(settings)
                else None
            )
            if _rebuild_missing_render_documents(settings, user_id=user_id, messages=page.messages):
                page = get_gmail_thread_message_page(
                    database_url,
                    user_id=user_id,
                    gmail_thread_id=group_id,
                    limit=limit,
                    offset=offset,
                ) or page
            if promote_body_fetch and page.incomplete_body_count > 0:
                _enqueue_reader_body_fetch_best_effort(
                    settings,
                    user_id=user_id,
                    gmail_thread_id=group_id,
                    priority=100,
                )
            return ThreadReaderResponse(
                entity_id=ai_group.id if ai_group else group_id,
                user_id=user_id,
                source="gmail",
                gmail_thread_id=group_id,
                subject=page.latest_subject,
                title=(ai_group.ai_title if ai_group else None) or page.latest_subject,
                summary=ai_group.ai_summary if ai_group else None,
                total_messages=page.total_messages,
                limit=limit,
                offset=offset,
                has_more=offset + len(page.messages) < page.total_messages,
                messages=_thread_messages_from_gmail(page.messages, settings=settings, user_id=user_id),
                content_revision=page.content_revision,
            )
        if not _ai_grouping_enabled(settings):
            return None

    canonical_messages = list_messages_for_gmail_thread(
        database_url,
        user_id=user_id,
        gmail_thread_id=group_id,
        maximum_messages=maximum_snapshot_messages,
        maximum_stored_bytes=maximum_snapshot_stored_bytes,
    )
    if canonical_messages:
        ai_group = (
            list_mail_groups_for_gmail_threads(database_url, user_id=user_id, gmail_thread_ids=[group_id]).get(group_id)
            if _ai_grouping_enabled(settings)
            else None
        )
        if _rebuild_missing_render_documents(settings, user_id=user_id, messages=canonical_messages):
            canonical_messages = list_messages_for_gmail_thread(
                database_url,
                user_id=user_id,
                gmail_thread_id=group_id,
                maximum_messages=maximum_snapshot_messages,
                maximum_stored_bytes=maximum_snapshot_stored_bytes,
            ) or canonical_messages
        if promote_body_fetch and any(_needs_body_fetch(message) for message in canonical_messages):
            _enqueue_reader_body_fetch_best_effort(
                settings,
                user_id=user_id,
                gmail_thread_id=group_id,
                priority=100,
            )
        messages = canonical_messages[offset : offset + limit]
        latest_message = max(canonical_messages, key=lambda item: item.internal_date or item.updated_at)
        return ThreadReaderResponse(
            entity_id=ai_group.id if ai_group else group_id,
            user_id=user_id,
            source="gmail",
            gmail_thread_id=group_id,
            subject=latest_message.subject,
            title=(ai_group.ai_title if ai_group else None) or latest_message.subject,
            summary=ai_group.ai_summary if ai_group else None,
            total_messages=len(canonical_messages),
            limit=limit,
            offset=offset,
            has_more=offset + len(messages) < len(canonical_messages),
            messages=_thread_messages_from_gmail(messages, settings=settings, user_id=user_id),
            content_revision=_gmail_thread_content_revision(canonical_messages),
        )

    if not _ai_grouping_enabled(settings):
        return None
    detail = get_mail_group_detail(database_url, user_id=user_id, group_id=group_id)
    if detail is None:
        return None
    if _rebuild_missing_render_documents(settings, user_id=user_id, messages=detail.messages):
        detail = get_mail_group_detail(database_url, user_id=user_id, group_id=group_id) or detail
    if promote_body_fetch and any(_needs_body_fetch(message) for message in detail.messages):
        _enqueue_reader_body_fetch_best_effort(
            settings,
            user_id=user_id,
            group_id=group_id,
            priority=100,
        )
    detail_messages = _expand_group_messages_to_canonical_threads(database_url, user_id=user_id, messages=detail.messages)
    if _rebuild_missing_render_documents(settings, user_id=user_id, messages=detail_messages):
        detail_messages = _expand_group_messages_to_canonical_threads(database_url, user_id=user_id, messages=detail.messages)
    messages = detail_messages[offset : offset + limit]
    return ThreadReaderResponse(
        entity_id=detail.group.id,
        user_id=user_id,
        source="gmail",
        gmail_thread_id=detail.group.id,
        subject=detail.group.ai_title,
        title=detail.group.ai_title,
        summary=detail.group.ai_summary,
        total_messages=len(detail_messages),
        limit=limit,
        offset=offset,
        has_more=offset + len(messages) < len(detail_messages),
        messages=_thread_messages_from_gmail(messages, settings=settings, user_id=user_id),
        content_revision=_gmail_thread_content_revision(detail_messages),
    )


def _build_mailbox_display_cluster_detail_response(
    settings: Settings,
    *,
    user_id: str,
    cluster_id: str,
    limit: int,
    offset: int,
    promote_body_fetch: bool = True,
) -> ThreadReaderResponse | None:
    database_url = str(settings.database_path)
    for mailbox_label in ("inbox", "all"):
        page = list_mailbox_thread_page(database_url, user_id=user_id, label=mailbox_label, limit=1000, since_iso=None)
        cluster_messages: list[GmailMessageRecord] = []
        for thread_id, messages in page.threads:
            cluster_key = _mailbox_display_cluster_key(thread_id=thread_id, messages=messages, mailbox_label=mailbox_label)
            if cluster_key and _mailbox_display_cluster_id(cluster_key) == cluster_id:
                cluster_messages.extend(messages)
        thread_ids = {message.gmail_thread_id or message.message_id for message in cluster_messages if message.gmail_thread_id or message.message_id}
        if len(thread_ids) < 2:
            continue
        detail_messages = sorted(cluster_messages, key=lambda item: item.internal_date or item.updated_at or "")
        if _rebuild_missing_render_documents(settings, user_id=user_id, messages=detail_messages):
            page = list_mailbox_thread_page(database_url, user_id=user_id, label=mailbox_label, limit=1000, since_iso=None)
            detail_messages = sorted(
                [
                    message
                    for thread_id, messages in page.threads
                    if (
                        (cluster_key := _mailbox_display_cluster_key(thread_id=thread_id, messages=messages, mailbox_label=mailbox_label))
                        and _mailbox_display_cluster_id(cluster_key) == cluster_id
                    )
                    for message in messages
                ],
                key=lambda item: item.internal_date or item.updated_at or "",
            )
        if promote_body_fetch and any(_needs_body_fetch(message) for message in detail_messages):
            for thread_id in sorted(thread_ids):
                _enqueue_body_fetch_for_gmail_thread(settings, user_id=user_id, gmail_thread_id=thread_id, priority=100)
        row = _gmail_row_from_mailbox_display_cluster(
            _mailbox_display_cluster_key(thread_id=next(iter(thread_ids)), messages=detail_messages, mailbox_label=mailbox_label) or cluster_id,
            detail_messages,
            mailbox_label=mailbox_label,
        )
        messages = detail_messages[offset : offset + limit]
        return ThreadReaderResponse(
            entity_id=cluster_id,
            user_id=user_id,
            source="gmail",
            gmail_thread_id=cluster_id,
            subject=row.title,
            title=row.title,
            summary=row.summary,
            total_messages=len(detail_messages),
            limit=limit,
            offset=offset,
            has_more=offset + len(messages) < len(detail_messages),
            messages=_thread_messages_from_gmail(messages, settings=settings, user_id=user_id),
            content_revision=_gmail_thread_content_revision(detail_messages),
        )
    return None


def enrich_group(
    settings: Settings,
    *,
    messages: list[GmailMessageRecord],
    group_key: str,
    generated_from_hash: str,
    use_ai: bool = False,
) -> dict[str, Any]:
    """Build deterministic grouping metadata without invoking an external model."""
    del settings, generated_from_hash, use_ai
    return _fallback_enrichment(messages, group_key)


def _persist_message_ai_titles(
    database_url: str,
    *,
    user_id: str,
    enrichment: dict[str, Any],
    messages: list[GmailMessageRecord],
    generated_at: str | None,
) -> None:
    if not generated_at:
        return
    titles = _message_title_map(enrichment.get("message_titles"), messages)
    if titles:
        update_gmail_message_ai_titles(database_url, user_id=user_id, titles=titles, generated_at=generated_at)


def _message_title_map(raw_titles: Any, messages: list[GmailMessageRecord]) -> dict[str, str]:
    by_id = {message.message_id: message for message in messages}
    titles: dict[str, str] = {}
    if isinstance(raw_titles, dict):
        items = raw_titles.items()
    elif isinstance(raw_titles, list):
        items = []
        for item in raw_titles:
            if isinstance(item, dict):
                items.append((item.get("message_id"), item.get("ai_title") or item.get("title")))
    else:
        items = []
    for raw_message_id, raw_title in items:
        message_id = str(raw_message_id or "")
        title = compact_text(str(raw_title or ""))
        message = by_id.get(message_id)
        if message is not None and title:
            titles[message_id] = _quality_message_title(message, title)[:180]
    return titles


def _quality_message_title(message: GmailMessageRecord, title: str) -> str:
    enrichment = attention_enrichment_payload(
        messages=[message],
        group_key=f"gmail-message:{message.message_id}",
        ai_output={"ai_title": title},
    )
    return compact_text(str(enrichment.get("ai_title") or title))


def _expand_group_messages_to_canonical_threads(
    database_url: str,
    *,
    user_id: str,
    messages: list[GmailMessageRecord],
) -> list[GmailMessageRecord]:
    thread_ids = list(
        dict.fromkeys(
            message.gmail_thread_id
            for message in messages
            if message.gmail_thread_id
        )
    )
    if not thread_ids:
        return messages
    expanded: dict[str, GmailMessageRecord] = {}
    for thread_id in thread_ids:
        for thread_message in list_messages_for_gmail_thread(database_url, user_id=user_id, gmail_thread_id=thread_id):
            expanded[thread_message.message_id] = thread_message
    if not expanded:
        return messages
    for message in messages:
        expanded.setdefault(message.message_id, message)
    return sorted(expanded.values(), key=lambda item: item.internal_date or item.updated_at or "")




def _enrichment_from_existing(group: MailGroupRecord) -> dict[str, Any]:
    return {
        "group_type": group.group_type,
        "ai_title": group.ai_title,
        "ai_summary": group.ai_summary,
        "labels": group.labels,
        "action_needed": group.action_needed,
        "action_type": group.action_type,
        "priority": group.priority,
        "timing_band": group.timing_band,
        "dashboard_visible": group.dashboard_visible,
        "message_titles": {},
        "classification_version": group.classification_version,
        "classification_json": group.classification or {},
        "classification_confidence": group.classification_confidence,
        "ranking_reason": group.ranking_reason,
        "suppression_reason": group.suppression_reason,
    }


def _classification_kwargs(enrichment: dict[str, Any], *, generated_at: str | None) -> dict[str, Any]:
    version = compact_text(str(enrichment.get("classification_version") or "")) or None
    classification = enrichment.get("classification_json")
    classified_at = generated_at or datetime.now(timezone.utc).isoformat()
    return {
        "classification_version": version,
        "classification": classification if isinstance(classification, dict) else {},
        "classification_confidence": _classification_confidence(enrichment.get("classification_confidence")),
        "ranking_reason": compact_text(str(enrichment.get("ranking_reason") or ""))[:500] or None,
        "suppression_reason": compact_text(str(enrichment.get("suppression_reason") or ""))[:500] or None,
        "classified_at": classified_at if version else None,
    }


def _classification_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _candidate_groups(messages: list[GmailMessageRecord]) -> dict[str, list[GmailMessageRecord]]:
    groups: dict[str, list[GmailMessageRecord]] = defaultdict(list)
    for message in messages:
        signals = message.extracted_signals
        domain = str(signals.get("sender_domain") or sender_domain(message.sender))
        signal_namespace = _signal_group_namespace(message, domain=domain)
        for signal_name in ["order_id", "ticket_id", "tracking_id", "invoice_id", "booking_id", "application_id"]:
            value = signals.get(signal_name)
            if isinstance(value, str) and value:
                groups[f"{signal_name}:{signal_namespace}:{value}"].append(message)
                break
        else:
            if reference_key := _conversation_reference_group_key(message, domain=domain):
                groups[reference_key].append(message)
            else:
                if message.gmail_thread_id:
                    groups[f"gmail-thread:{message.gmail_thread_id}"].append(message)
                else:
                    subject = str(signals.get("normalized_subject") or message.subject or message.message_id)[:120]
                    groups[f"subject-domain:{domain}:{subject}"].append(message)
    return dict(groups)


def _merge_group_messages(messages: list[GmailMessageRecord]) -> list[GmailMessageRecord]:
    by_id: dict[str, GmailMessageRecord] = {}
    for message in messages:
        by_id[message.message_id] = message
    return sorted(by_id.values(), key=lambda item: item.internal_date or item.updated_at or "", reverse=True)


def _signal_group_namespace(message: GmailMessageRecord, *, domain: str) -> str:
    """Keep exact-reference groups stable across sender-domain variants for one provider."""
    text = " ".join([domain, message.sender or ""]).lower()
    overrides = [
        (("northstarbank", "northstar.bank", "northstarfx", "northstar"), "northstar-bank"),
        (("ccilindia", "fxclear", "fxnoreply"), "ccil-fx-retail"),
        (("interactivebrokers", "ibkr"), "interactive-brokers"),
        (("cityflo",), "cityflo"),
        (("tatastarbucks", "starbucks"), "starbucks-india"),
        (("google",), "google"),
        (("sbi",), "sbi"),
        (("hsbc",), "hsbc"),
        (("openai", "chatgpt"), "openai"),
        (("amazon web services", "amazonaws", "aws"), "amazon-web-services"),
        (("flipkart",), "flipkart"),
        (("railway",), "railway"),
    ]
    for needles, namespace in overrides:
        if any(needle in text for needle in needles):
            return namespace
    return domain


def _entity_lifecycle_group_key(message: GmailMessageRecord, *, domain: str) -> str | None:
    subject = str(message.extracted_signals.get("normalized_subject") or message.subject or "")
    _facts, classification = deterministic_classification([message])
    if classification.workflow_family in {"other", "conversation", "marketing", "newsletter"} and not classification.requires_user_action:
        return None
    sender_slug = _sender_slug(message.sender)
    domain_slug = _domain_slug(domain)
    identity_slug = sender_slug if sender_slug and sender_slug not in GENERIC_SENDER_SLUGS else domain_slug
    if not identity_slug:
        return None
    topic_slug = sender_slug if sender_slug and sender_slug not in GENERIC_SENDER_SLUGS else _topic_slug(subject, sender_slug=sender_slug, domain_slug=domain_slug)
    if not topic_slug:
        return None
    return f"entity-lifecycle:{domain}:{identity_slug}:{topic_slug}"


def _conversation_reference_group_key(message: GmailMessageRecord, *, domain: str) -> str | None:
    references = " ".join(
        str(message.headers.get(key) or "")
        for key in ["references", "References", "in-reply-to", "In-Reply-To"]
    )
    ids = re.findall(r"<([^>]+)>", references)
    if not ids:
        return None
    return f"mail-reference:{domain}:{hashlib.sha1(ids[-1].encode('utf-8')).hexdigest()[:24]}"


def _cross_thread_subject_group_key(message: GmailMessageRecord, *, domain: str) -> str | None:
    subject = str(message.extracted_signals.get("normalized_subject") or message.subject or "").strip()
    if not subject:
        return None
    labels = {label.upper() for label in message.label_ids}
    _facts, classification = deterministic_classification([message])
    low_signal = classification.workflow_family in {"other", "conversation", "marketing", "newsletter"} and not classification.requires_user_action
    if "SENT" not in labels and low_signal:
        return None
    topic = _topic_slug(subject, sender_slug=_sender_slug(message.sender), domain_slug=_domain_slug(domain))
    if not topic:
        return None
    return f"subject-domain:{domain}:{topic}"


def _sender_slug(sender: str | None) -> str:
    display_name, email_address = parseaddr(sender or "")
    source = display_name or email_address.split("@", 1)[0] if email_address else sender or ""
    return _slug(source)


def _domain_slug(domain: str) -> str:
    parts = [part for part in domain.lower().split(".") if part and part not in {"co", "com", "in", "net", "org"}]
    return _slug(parts[0] if parts else domain)


def _topic_slug(subject: str, *, sender_slug: str, domain_slug: str) -> str:
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", subject.lower())
        if len(token) > 2 and token not in TOPIC_STOP_WORDS
    ]
    if not tokens:
        return ""
    anchor_tokens = [token for token in tokens if token in {sender_slug, domain_slug}]
    if anchor_tokens:
        return "-".join(dict.fromkeys(anchor_tokens))[:80]
    return "-".join(dict.fromkeys(tokens[:3]))[:80]


def _slug(value: str | None) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", (value or "").lower()))[:80]


def _fallback_enrichment(messages: list[GmailMessageRecord], group_key: str) -> dict[str, Any]:
    return attention_enrichment_payload(messages=messages, group_key=group_key)


def _group_hash(messages: list[GmailMessageRecord]) -> str:
    parts = [f"{message.message_id}:{message.history_id}:{message.body_hash}:{message.internal_date}" for message in sorted(messages, key=lambda item: item.message_id)]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _member_reason(message: GmailMessageRecord) -> str:
    if message.gmail_thread_id:
        return "gmail_thread"
    signals = message.extracted_signals
    for key in ["order_id", "ticket_id", "tracking_id", "invoice_id", "booking_id", "application_id"]:
        if signals.get(key):
            return key
    return "subject_domain"


def _attention_item_from_group(group: MailGroupRecord) -> AttentionItem:
    action_type = "external" if group.action_needed else "none"
    classification = group.classification if isinstance(group.classification, dict) else {}
    terminal = is_terminal_classification(classification)
    confidence = group.classification_confidence
    action_confidence = "high" if confidence >= 0.75 else "medium" if confidence >= 0.45 else "low"
    return AttentionItem(
        id=f"mail-group:{group.id}",
        entity_id=group.id,
        user_id=group.user_id,
        need_type="decision" if group.action_needed else "awareness",
        action_type=action_type,
        effort_level="quick",
        timing_band=group.timing_band if group.timing_band in {"now", "today", "later", "hidden"} else "later",
        action_confidence=action_confidence,
        primary_action=group.action_type or "open",
        fallback_action="open",
        title=group.ai_title,
        why_this_is_here=group.ranking_reason or group.ai_summary,
        detail=AttentionItemDetail(
            body=[group.ai_summary],
            action_label="Open group",
            source_label="Gmail",
        ),
        importance_level="high" if group.priority >= 70 else "medium" if group.priority >= 35 else "low",
        lifecycle_state="active" if group.action_needed else "resolved" if terminal else "scheduled",
        current_state="open" if group.action_needed else "done" if terminal else "waiting",
        source="gmail",
        gmail_thread_id=group.id,
        gmail_thread_action=None,
        trace_id=f"mail-group:{group.id}",
        created_at=group.created_at,
    )


def _attention_item_from_manual_task(task: ManualTaskRecord) -> AttentionItem:
    notes = (task.notes or "").strip()
    body = notes or "Manual to-do."
    return AttentionItem(
        id=f"manual-task:{task.id}",
        entity_id=task.entity_id,
        user_id=task.user_id,
        need_type="decision",
        action_type="none",
        effort_level="quick",
        timing_band=task.section if task.section in {"now", "today", "later", "hidden"} else "today",
        action_confidence="high",
        primary_action="open",
        fallback_action="open",
        title=task.title,
        why_this_is_here=body,
        detail=AttentionItemDetail(
            body=[body],
            action_label="Mark done",
            source_label="Manual",
        ),
        due_at=task.due_at,
        importance_level="medium",
        lifecycle_state="active",
        current_state="open",
        source="manual",
        gmail_thread_id=None,
        gmail_thread_action=None,
        trace_id=f"manual-task:{task.id}",
        created_at=task.created_at,
    )


def _append_feed_item(feed: FeedResponse, item: AttentionItem) -> None:
    if item.timing_band == "now":
        feed.now.append(item)
    elif item.timing_band == "today":
        feed.today.append(item)
    elif item.timing_band != "hidden":
        feed.worth_knowing.append(item)


def _outcome_suppresses(outcome: EntityOutcomeRecord | None) -> bool:
    return outcome is not None and outcome.outcome_type in {"complete", "dismiss"}


def _group_has_dashboard_visible_mail(messages: list[GmailMessageRecord]) -> bool:
    for message in messages:
        labels = {label.upper() for label in message.label_ids}
        if labels & {"TRASH", "SPAM", "DRAFT", "SENT"}:
            continue
        return True
    return False


def _gmail_row_from_canonical_thread(
    thread_id: str,
    messages: list[GmailMessageRecord],
    mailbox_label: str,
    ai_group: MailGroupRecord | None,
) -> GmailThreadRow:
    # The label/search decides whether the conversation is eligible. Gmail then
    # renders and orders the canonical whole conversation, including a newer sent
    # reply in an Inbox-retained thread.
    row_messages = messages
    latest_message = max(row_messages, key=lambda item: item.internal_date or item.updated_at)
    participants = _mailbox_display_participants(row_messages, mailbox_label)
    display_sender = _mailbox_display_sender(latest_message, mailbox_label)
    labels = sorted({label for message in row_messages for label in message.label_ids})
    action_type = "none"
    if ai_group is not None and ai_group.action_type in {"pay", "reply", "confirm", "track", "review", "open", "none"}:
        action_type = ai_group.action_type
    presentation_status = _presentation_status(ai_group)
    title = (ai_group.ai_title if ai_group is not None else None) or latest_message.subject
    summary = (ai_group.ai_summary if ai_group is not None else None) or latest_message.snippet
    projection = next(
        (message for message in row_messages if message.mailbox_message_count is not None),
        None,
    )
    attachment_count = (
        max(0, int(projection.mailbox_attachment_count or 0))
        if projection is not None
        else sum(len(gmail_attachments_for_message(message)) for message in row_messages)
    )
    message_count = (
        max(1, int(projection.mailbox_message_count or 1))
        if projection is not None
        else max(1, len(row_messages))
    )
    body_ready = (
        bool(projection.mailbox_body_ready)
        if projection is not None
        else _gmail_thread_body_ready(row_messages)
    )
    content_revision = (
        str(projection.mailbox_content_revision)
        if projection is not None and projection.mailbox_content_revision
        else _gmail_thread_content_revision(row_messages)
    )
    return GmailThreadRow(
        thread_id=thread_id,
        entity_id=ai_group.id if ai_group is not None else thread_id,
        title=title,
        href=f"/v1/mailbox/threads/{thread_id}",
        latest_source_record_id=latest_message.message_id,
        latest_received_at=latest_message.internal_date or latest_message.updated_at,
        latest_message_at=latest_message.internal_date or latest_message.updated_at,
        latest_subject=latest_message.subject,
        latest_sender=latest_message.sender,
        sender=display_sender,
        participants=participants,
        message_count=message_count,
        body_ready=body_ready,
        content_revision=content_revision,
        summary=summary,
        ai_group_id=ai_group.id if ai_group is not None else None,
        ai_title=ai_group.ai_title if ai_group is not None else None,
        ai_summary=ai_group.ai_summary if ai_group is not None else None,
        snippet=latest_message.snippet,
        has_attachments=attachment_count > 0,
        attachment_count=attachment_count,
        label_ids=labels,
        labels=labels,
        unread="UNREAD" in {label.upper() for label in labels},
        action_needed=bool(ai_group and ai_group.action_needed),
        action_type=action_type,  # type: ignore[arg-type]
        action_type_key=action_type,  # type: ignore[arg-type]
        priority=ai_group.priority if ai_group is not None else 0,
        dashboard_visible=bool(ai_group and ai_group.dashboard_visible),
        current_state="open" if ai_group is not None and ai_group.action_needed else "waiting",
        lifecycle_state="active" if ai_group is not None and ai_group.action_needed else "scheduled",
        lifecycle_updates=[
            {
                "source_record_id": message.message_id,
                "received_at": message.internal_date or message.updated_at,
                "subject": message.subject or title,
                "sender": message.sender,
                "summary": message.snippet,
            }
            for message in row_messages[-3:]
        ],
        children=[_gmail_child_row_from_message(message, mailbox_label=mailbox_label) for message in row_messages],
        enrichment_status=ai_group.enrichment_status if ai_group is not None else "ready",
        presentation_status=presentation_status,  # type: ignore[arg-type]
    )


def _gmail_row_from_visible_group(group: VisibleMailGroupRecord, messages: list[GmailMessageRecord], mailbox_label: str) -> GmailThreadRow:
    row_messages = sorted(_visible_group_mailbox_messages(messages, mailbox_label), key=lambda item: item.internal_date or item.updated_at or "")
    latest_message = max(row_messages, key=lambda item: item.internal_date or item.updated_at)
    participants = _mailbox_display_participants(row_messages, mailbox_label)
    display_sender = _visible_group_display_sender(group, messages, row_messages, latest_message, mailbox_label)
    labels = sorted({label for message in row_messages for label in message.label_ids})
    attachment_count = sum(len(gmail_attachments_for_message(message)) for message in row_messages)
    action_type = _cluster_action_type(group.workflow_type)
    mailbox_thread_id = group.source_group_id or group.id
    return GmailThreadRow(
        thread_id=mailbox_thread_id,
        entity_id=group.id,
        title=group.title or latest_message.subject,
        href=f"/v1/mailbox/threads/{mailbox_thread_id}",
        latest_source_record_id=latest_message.message_id,
        latest_received_at=latest_message.internal_date or latest_message.updated_at,
        latest_message_at=latest_message.internal_date or latest_message.updated_at,
        latest_subject=latest_message.subject,
        latest_sender=latest_message.sender,
        sender=display_sender,
        participants=participants,
        message_count=max(1, len(row_messages)),
        body_ready=_gmail_thread_body_ready(messages),
        content_revision=_gmail_thread_content_revision(messages),
        summary=group.summary or latest_message.snippet,
        ai_group_id=group.source_group_id,
        ai_title=group.title,
        ai_summary=group.summary,
        snippet=latest_message.snippet,
        has_attachments=attachment_count > 0,
        attachment_count=attachment_count,
        label_ids=labels,
        labels=labels,
        unread="UNREAD" in {label.upper() for label in labels},
        action_needed=False,
        action_type=action_type,  # type: ignore[arg-type]
        action_type_key=action_type,  # type: ignore[arg-type]
        priority=max(0, min(100, round(group.confidence * 100))),
        dashboard_visible=False,
        current_state="waiting",
        lifecycle_state="scheduled",
        lifecycle_updates=[
            {
                "source_record_id": message.message_id,
                "received_at": message.internal_date or message.updated_at,
                "subject": message.subject or group.title,
                "sender": message.sender,
                "summary": message.snippet,
            }
            for message in row_messages[-3:]
        ],
        children=[_gmail_child_row_from_message(message, mailbox_label=mailbox_label) for message in row_messages],
        enrichment_status="ready",
        presentation_status="ai_ready",
    )


def _visible_group_has_mailbox_messages(messages: list[GmailMessageRecord], mailbox_label: str) -> bool:
    return bool(_visible_group_mailbox_messages(messages, mailbox_label))


def _visible_group_mailbox_messages(messages: list[GmailMessageRecord], mailbox_label: str) -> list[GmailMessageRecord]:
    if mailbox_label in {"inbox", "all"}:
        return [
            message
            for message in messages
            if _message_matches_mailbox_label(message, mailbox_label)
            and not ({"SENT", "DRAFT", "TRASH"} & {label.upper() for label in message.label_ids})
        ]
    return [message for message in messages if _message_matches_mailbox_label(message, mailbox_label)]


def _visible_group_display_sender(
    group: VisibleMailGroupRecord,
    all_messages: list[GmailMessageRecord],
    row_messages: list[GmailMessageRecord],
    latest_message: GmailMessageRecord,
    mailbox_label: str,
) -> str | None:
    if mailbox_label == "sent":
        return _mailbox_display_sender(latest_message, mailbox_label)
    canonical = compact_text(group.canonical_entity or "")
    if canonical and not _visible_group_canonical_looks_like_user(canonical, all_messages):
        return canonical
    inbound_messages = [message for message in row_messages if "SENT" not in {label.upper() for label in message.label_ids}]
    if inbound_messages:
        latest_inbound = max(inbound_messages, key=lambda item: item.internal_date or item.updated_at)
        return _mailbox_display_sender(latest_inbound, mailbox_label)
    return _mailbox_display_sender(latest_message, mailbox_label) or canonical or None


def _visible_group_canonical_looks_like_user(canonical: str, messages: list[GmailMessageRecord]) -> bool:
    normalized_canonical = canonical.lower()
    for message in messages:
        labels = {label.upper() for label in message.label_ids}
        if "SENT" not in labels:
            continue
        values = _sender_identity_values(message.sender)
        if normalized_canonical in values:
            return True
    return False


def _sender_identity_values(sender: str | None) -> set[str]:
    name, address = parseaddr(sender or "")
    values = {compact_text(value).lower() for value in [name, address, sender or ""] if compact_text(value)}
    if "@" in address:
        local = address.split("@", 1)[0]
        values.add(local.lower())
    return values


def _mailbox_display_cluster_rows(entries: list[MailboxDisplayClusterEntry], *, mailbox_label: str) -> list[GmailThreadRow]:
    if mailbox_label not in {"inbox", "all"}:
        return [entry.row for entry in entries]

    buckets: dict[str, list[MailboxDisplayClusterEntry]] = defaultdict(list)
    for entry in entries:
        if entry.cluster_key:
            buckets[entry.cluster_key].append(entry)

    clustered_thread_ids: set[str] = set()
    rows: list[GmailThreadRow] = []
    for cluster_key, bucket_entries in buckets.items():
        thread_ids = {entry.thread_id for entry in bucket_entries}
        if len(thread_ids) < 2:
            continue
        clustered_thread_ids.update(thread_ids)
        messages = [message for entry in bucket_entries for message in entry.messages]
        rows.append(_gmail_row_from_mailbox_display_cluster(cluster_key, messages, mailbox_label=mailbox_label))

    rows.extend(entry.row for entry in entries if entry.thread_id not in clustered_thread_ids)
    return rows


def _gmail_row_from_mailbox_display_cluster(cluster_key: str, messages: list[GmailMessageRecord], *, mailbox_label: str) -> GmailThreadRow:
    matching_messages = [message for message in messages if _message_matches_mailbox_label(message, mailbox_label)]
    row_messages = sorted(matching_messages or messages, key=lambda item: item.internal_date or item.updated_at or "")
    latest_message = max(row_messages, key=lambda item: item.internal_date or item.updated_at)
    facts, classification = deterministic_classification(row_messages)
    family = _mailbox_cluster_family(cluster_key) or classification.workflow_family
    cluster_id = _mailbox_display_cluster_id(cluster_key)
    labels = sorted({label for message in row_messages for label in message.label_ids})
    attachment_count = sum(len(gmail_attachments_for_message(message)) for message in row_messages)
    provider_title = _mailbox_cluster_provider_title(row_messages, facts.provider)
    title = _mailbox_cluster_title(provider_title, family, cluster_key)
    summary = _mailbox_cluster_summary(provider_title, family, len({m.gmail_thread_id or m.message_id for m in row_messages}))
    action_type = _cluster_action_type(family)
    return GmailThreadRow(
        thread_id=cluster_id,
        entity_id=cluster_id,
        title=title,
        href=f"/v1/mailbox/threads/{cluster_id}",
        latest_source_record_id=latest_message.message_id,
        latest_received_at=latest_message.internal_date or latest_message.updated_at,
        latest_message_at=latest_message.internal_date or latest_message.updated_at,
        latest_subject=latest_message.subject,
        latest_sender=latest_message.sender,
        sender=provider_title,
        participants=_mailbox_display_participants(row_messages, mailbox_label),
        message_count=max(1, len(row_messages)),
        body_ready=_gmail_thread_body_ready(messages),
        content_revision=_gmail_thread_content_revision(messages),
        summary=summary,
        ai_group_id=None,
        ai_title=title,
        ai_summary=summary,
        snippet=latest_message.snippet,
        has_attachments=attachment_count > 0,
        attachment_count=attachment_count,
        label_ids=labels,
        labels=labels,
        unread="UNREAD" in {label.upper() for label in labels},
        action_needed=classification.requires_user_action,
        action_type=action_type,  # type: ignore[arg-type]
        action_type_key=action_type,  # type: ignore[arg-type]
        priority=0,
        dashboard_visible=False,
        current_state="open" if classification.requires_user_action else "waiting",
        lifecycle_state="active" if classification.requires_user_action else "scheduled",
        lifecycle_updates=[
            {
                "source_record_id": message.message_id,
                "received_at": message.internal_date or message.updated_at,
                "subject": message.subject or title,
                "sender": message.sender,
                "summary": message.snippet,
            }
            for message in row_messages[-3:]
        ],
        children=[_gmail_child_row_from_message(message, mailbox_label=mailbox_label) for message in row_messages],
        enrichment_status="ready",
        presentation_status="ai_ready",
    )


def _mailbox_display_cluster_key(*, thread_id: str, messages: list[GmailMessageRecord], mailbox_label: str) -> str | None:
    if mailbox_label not in {"inbox", "all"} or not messages:
        return None
    try:
        facts, classification = deterministic_classification(messages)
    except Exception:
        return None
    provider = facts.provider
    if not provider or provider == "unknown":
        return None
    if _is_safe_provider_stream_display_cluster(messages, classification, facts.text):
        return f"newsletter:provider-stream:{provider}"
    family = _mailbox_display_cluster_family(classification.workflow_family, facts.text)
    if not family:
        return None
    topic = _mailbox_cluster_topic(family=family, text=facts.text)
    if family == "support_case" and topic == "transfer":
        family = "financial_transfer"
    if family == "logistics" and topic == "logistics":
        return None
    reference = facts.reference_id
    if reference and not (family == "financial_transfer" and reference.startswith("ticket_id:")):
        return f"{family}:ref:{provider}:{reference}:{topic}"
    if family == "billing" and topic != "generic":
        return f"{family}:window:{provider}:{topic}:{_mailbox_cluster_month(facts.latest_at)}"
    return None


def _is_safe_newsletter_display_cluster(messages: list[GmailMessageRecord], classification: Any) -> bool:
    if bool(getattr(classification, "requires_user_action", False)):
        return False
    for message in messages:
        labels = {label.upper() for label in message.label_ids}
        if labels & {"SENT", "DRAFT", "TRASH", "SPAM"}:
            return False
        if labels & {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}:
            return True
        signals = message.extracted_signals if isinstance(message.extracted_signals, dict) else {}
        if signals.get("list_id") or signals.get("unsubscribe_url"):
            return True
    return False


def _is_safe_provider_stream_display_cluster(messages: list[GmailMessageRecord], classification: Any, text: str) -> bool:
    if bool(getattr(classification, "requires_user_action", False)) or _mailbox_text_is_job_alert(text):
        return False
    for message in messages:
        labels = {label.upper() for label in message.label_ids}
        if labels & {"SENT", "DRAFT", "TRASH", "SPAM"}:
            return False
    if _is_safe_newsletter_display_cluster(messages, classification):
        return True
    lowered = text.lower()
    stream_words = (
        "welcome",
        "weekly",
        "newsletter",
        "product update",
        "onboarding",
        "setup",
        "getting started",
        "tips",
        "power moves",
        "aha moment",
    )
    if any(word in lowered for word in stream_words):
        return True
    workflow_family = str(getattr(classification, "workflow_family", "") or "")
    if workflow_family in {"application", "financial_transfer", "support_case", "billing", "logistics"}:
        return False
    return any(
        "CATEGORY_UPDATES" in {label.upper() for label in message.label_ids}
        for message in messages
    )


def _mailbox_display_cluster_family(family: str, text: str) -> str:
    lowered = text.lower()
    if _mailbox_text_is_job_alert(lowered):
        return ""
    if family == "billing" and TRAVEL_TOPIC_RE.search(lowered):
        family = "logistics"
    return family if family in MAILBOX_DISPLAY_CLUSTER_WORKFLOW_TYPES else ""


def _mailbox_text_is_job_alert(text: str) -> bool:
    lowered = text.lower()
    return "job alert" in lowered or "matches your alert settings" in lowered


def _mailbox_cluster_family(cluster_key: str) -> str | None:
    family = cluster_key.split(":", 1)[0].strip()
    return family if family in MAILBOX_DISPLAY_CLUSTER_WORKFLOW_TYPES else None


def _mailbox_cluster_topic(*, family: str, text: str) -> str:
    lowered = text.lower()
    if family == "financial_transfer":
        if TRANSFER_TOPIC_RE.search(lowered) or "swift" in lowered:
            return "transfer"
        return "financial"
    if family == "support_case":
        if TRANSFER_TOPIC_RE.search(lowered):
            return "transfer"
        if "card" in lowered or "consent" in lowered or "privacy" in lowered:
            return "card"
        return "case"
    if family == "logistics":
        if TRAVEL_TOPIC_RE.search(lowered):
            return "travel"
        if any(token in lowered for token in ("order", "shipment", "tracking", "delivery", "delivered")):
            return "delivery"
        return "logistics"
    if family == "billing":
        if "statement" in lowered:
            return "statement"
        if "invoice" in lowered or "receipt" in lowered or "payment" in lowered:
            return "payment"
        return "generic"
    if family == "account_security":
        return "security"
    if family == "application":
        if "cost" in lowered or "financial aid" in lowered:
            return "costs"
        if "reconsideration" in lowered:
            return "reconsideration"
        return "application"
    return "generic"


def _mailbox_display_cluster_id(cluster_key: str) -> str:
    digest = hashlib.sha1(cluster_key.encode("utf-8")).hexdigest()[:20]
    return f"{MAILBOX_DISPLAY_CLUSTER_PREFIX}{digest}"


def _is_mailbox_display_cluster_id(value: str) -> bool:
    return value.startswith(MAILBOX_DISPLAY_CLUSTER_PREFIX)


def _mailbox_cluster_month(value: str | None) -> str:
    if not value:
        return "unknown"
    parsed = _parse_date(value)
    return f"{parsed.year}-{parsed.month:02d}"


def _mailbox_cluster_provider_title(messages: list[GmailMessageRecord], provider: str) -> str:
    provider_title_overrides = {
        "northstar": "Northstar Bank",
        "sbi": "SBI",
        "hsbc": "HSBC",
        "ibkr": "Interactive Brokers",
        "interactivebrokers": "Interactive Brokers",
        "cityflo": "Cityflo",
    }
    if provider in provider_title_overrides:
        return provider_title_overrides[provider]
    provider_title = provider.replace("-", " ").title()
    names: list[str] = []
    for message in messages:
        name, address = parseaddr(message.sender or "")
        candidate = compact_text(name or "")
        if not candidate or candidate.lower() in GENERIC_SENDER_SLUGS:
            candidate = compact_text((address.split("@", 1)[1].split(".", 1)[0] if "@" in address else ""))
        candidate = _provider_title_from_sender_name(candidate, provider_title) or candidate
        if candidate:
            names.append(candidate)
    if names:
        counts: dict[str, int] = defaultdict(int)
        canonical: dict[str, str] = {}
        for name in names:
            key = re.sub(r"[^a-z0-9]+", "", name.lower())
            counts[key] += 1
            canonical.setdefault(key, name)
        best_key = max(counts, key=lambda key: (counts[key], len(canonical[key])))
        return canonical[best_key]
    return provider_title


def _provider_title_from_sender_name(name: str, provider_title: str) -> str | None:
    if not name or not provider_title:
        return None
    provider_pattern = re.escape(provider_title)
    if re.search(rf"(?i)\b{provider_pattern}\b", name):
        return provider_title
    normalized_provider = re.sub(r"[^a-z0-9]+", "", provider_title.lower())
    normalized_name = re.sub(r"[^a-z0-9]+", "", name.lower())
    if normalized_provider and normalized_provider in normalized_name:
        return provider_title
    return None


def _mailbox_cluster_title(provider_title: str, family: str, cluster_key: str) -> str:
    topic = cluster_key.split(":")[-2] if ":window:" in cluster_key else cluster_key.split(":")[-1]
    labels = {
        "financial_transfer": "transfer updates",
        "support_case": "support case",
        "billing": "billing updates",
        "logistics": "travel updates" if topic == "travel" else "delivery updates",
        "account_security": "security alerts",
        "application": "application updates",
        "newsletter": "updates",
        "marketing": "updates",
    }
    return f"{provider_title} {labels.get(family, 'updates')}"


def _mailbox_cluster_summary(provider_title: str, family: str, thread_count: int) -> str:
    label = {
        "financial_transfer": "transfer",
        "support_case": "support case",
        "billing": "billing",
        "logistics": "travel or delivery",
        "account_security": "security",
        "application": "application",
        "newsletter": "newsletter",
        "marketing": "product update",
    }.get(family, "mailbox")
    return f"{thread_count} related {provider_title} {label} emails."


def _cluster_action_type(family: str) -> str:
    if family == "billing":
        return "pay"
    if family == "logistics":
        return "track"
    if family == "account_security":
        return "review"
    return "open"


def _sort_mailbox_rows(rows: list[GmailThreadRow]) -> list[GmailThreadRow]:
    return sorted(rows, key=lambda row: (row.latest_received_at or "", row.thread_id), reverse=True)


def _gmail_child_row_from_message(message: GmailMessageRecord, *, mailbox_label: str = "inbox") -> GmailThreadChildRow:
    return GmailThreadChildRow(
        message_id=message.message_id,
        gmail_thread_id=message.gmail_thread_id,
        sender=_mailbox_display_sender(message, mailbox_label),
        subject=message.subject,
        # Historical generated titles may still exist in a migrated database;
        # the no-AI mailbox contract returns the original Gmail subject only.
        ai_title=None,
        snippet=message.snippet,
        received_at=message.internal_date or message.updated_at,
        label_ids=message.label_ids,
        labels=message.label_ids,
        unread="UNREAD" in {label.upper() for label in message.label_ids},
    )


def _presentation_status(ai_group: MailGroupRecord | None) -> str:
    if ai_group is None:
        return "fallback"
    if ai_group.enrichment_status == "pending":
        return "ai_pending"
    if ai_group.enrichment_status == "ready" and ai_group.ai_title and ai_group.ai_summary:
        return "ai_ready"
    return "fallback"


def _message_matches_mailbox_label(message: GmailMessageRecord, label: str) -> bool:
    labels = {item.upper() for item in message.label_ids}
    if label == "all":
        return not ({"SPAM", "TRASH"} & labels)
    if label == "inbox":
        return "INBOX" in labels
    if label == "sent":
        return "SENT" in labels
    if label == "drafts":
        return "DRAFT" in labels
    if label == "spam":
        return "SPAM" in labels
    if label == "trash":
        return "TRASH" in labels
    if label == "starred":
        return "STARRED" in labels
    if label == "archive":
        return not ({"INBOX", "SENT", "DRAFT", "SPAM", "TRASH"} & labels)
    return True


def _dedupe_groups_by_messages(
    groups: list[MailGroupRecord],
    group_messages: dict[str, list[GmailMessageRecord]],
) -> list[MailGroupRecord]:
    seen_message_ids: set[str] = set()
    deduped: list[MailGroupRecord] = []
    for group in sorted(groups, key=_group_visibility_score, reverse=True):
        message_ids = {message.message_id for message in group_messages.get(group.id, [])}
        if message_ids and message_ids & seen_message_ids:
            continue
        seen_message_ids.update(message_ids)
        deduped.append(group)
    order = {group.id: index for index, group in enumerate(groups)}
    deduped.sort(key=lambda group: order.get(group.id, len(order)))
    return deduped


def _collapse_dashboard_workflow_groups(
    groups: list[MailGroupRecord],
    group_messages: dict[str, list[GmailMessageRecord]],
) -> list[MailGroupRecord]:
    clusters: dict[str, list[MailGroupRecord]] = defaultdict(list)
    for group in groups:
        messages = group_messages.get(group.id, [])
        workflow_key = workflow_cluster_key(
            group.group_type,
            _group_classification_json(group, messages),
            group.latest_message_at or group.updated_at,
        )
        if workflow_key:
            clusters[workflow_key].append(group)

    suppressed_ids: set[str] = set()
    for cluster_groups in clusters.values():
        if len(cluster_groups) < 2:
            continue

        terminal_groups = [
            group
            for group in cluster_groups
            if is_terminal_classification(_group_classification_json(group, group_messages.get(group.id, [])))
        ]
        if terminal_groups:
            terminal = max(terminal_groups, key=_dashboard_workflow_sort_key)
            for group in cluster_groups:
                if group.id == terminal.id:
                    continue
                if _dashboard_superseded_by_terminal(group, terminal, group_messages.get(group.id, [])):
                    suppressed_ids.add(group.id)
            for group in terminal_groups:
                if group.id != terminal.id:
                    suppressed_ids.add(group.id)
            continue

        representative = max(cluster_groups, key=_dashboard_workflow_sort_key)
        for group in cluster_groups:
            if group.id == representative.id:
                continue
            classification = _group_classification_json(group, group_messages.get(group.id, []))
            if not group.action_needed and is_status_noise_classification(classification):
                suppressed_ids.add(group.id)

    return [group for group in groups if group.id not in suppressed_ids]


def _group_classification_json(group: MailGroupRecord, messages: list[GmailMessageRecord]) -> dict[str, Any]:
    if isinstance(group.classification, dict) and group.classification:
        return group.classification
    if messages:
        facts, classification = deterministic_classification(messages)
        return {
            **classification.to_json(),
            "facts": facts_to_json(facts),
            "group_key": group.group_key,
        }
    return {
        "workflow_family": group.group_type,
        "workflow_state": "needs_user_action" if group.action_needed else "informational",
        "requires_user_action": group.action_needed,
        "terminal_state": False,
        "confidence": 0.0,
    }


def _dashboard_superseded_by_terminal(
    group: MailGroupRecord,
    terminal: MailGroupRecord,
    messages: list[GmailMessageRecord],
) -> bool:
    if group.id == terminal.id:
        return False
    classification = _group_classification_json(group, messages)
    if is_status_noise_classification(classification):
        return True
    if classification.get("workflow_family") not in {None, "", "other", "conversation"}:
        return True
    return not group.action_needed


def _dashboard_workflow_sort_key(group: MailGroupRecord) -> tuple[int, int, str]:
    visible_rank = 0 if group.timing_band == "hidden" else 1
    return (visible_rank, group.priority, group.latest_message_at or group.updated_at)


def _group_visibility_score(group: MailGroupRecord) -> tuple[int, int, int, str]:
    source_score = 2 if group.membership_source == "ai_batch" else 1
    visible_score = 1 if group.dashboard_visible else 0
    action_score = 1 if group.action_needed else 0
    return (source_score, visible_score, action_score, group.updated_at)


def _participants(messages: list[GmailMessageRecord]) -> list[str]:
    seen: set[str] = set()
    participants: list[str] = []
    for message in messages:
        sender = message.sender or ""
        name, address = parseaddr(sender)
        value = compact_text(name or address or sender)
        if value and value.lower() not in seen:
            seen.add(value.lower())
            participants.append(value)
    return participants[:6]


def _mailbox_display_sender(message: GmailMessageRecord, mailbox_label: str) -> str | None:
    if mailbox_label == "sent":
        return _first_recipient_display(message) or message.sender
    return message.sender


def _mailbox_display_participants(messages: list[GmailMessageRecord], mailbox_label: str) -> list[str]:
    if mailbox_label != "sent":
        return _participants(messages)

    participants = _recipient_participants(messages)
    if participants:
        return participants
    return _participants(messages)


def _recipient_participants(messages: list[GmailMessageRecord]) -> list[str]:
    seen: set[str] = set()
    participants: list[str] = []
    for message in messages:
        for value in _recipient_display_values(message):
            normalized = value.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            participants.append(value)
            if len(participants) >= 6:
                return participants
    return participants


def _first_recipient_display(message: GmailMessageRecord) -> str | None:
    values = _recipient_display_values(message)
    return values[0] if values else None


def _recipient_display_values(message: GmailMessageRecord) -> list[str]:
    recipients = message.recipients if isinstance(message.recipients, dict) else {}
    raw_to = recipients.get("to")
    values = _address_header_values(raw_to)
    display_values: list[str] = []
    for name, address in getaddresses(values):
        value = compact_text(name or address)
        if value:
            display_values.append(value)
    return display_values


def _address_header_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in (str(item).strip() for item in value) if item]
    return []


def gmail_attachments_for_message(message: GmailMessageRecord) -> list[ThreadAttachment]:
    if message.attachment_descriptors:
        attachments: list[ThreadAttachment] = []
        for index, descriptor in enumerate(message.attachment_descriptors):
            attachment_id = str(descriptor.get("attachment_id") or "").strip()
            if not attachment_id:
                continue
            filename = str(descriptor.get("filename") or "").strip()
            try:
                size = max(0, int(descriptor.get("size") or 0))
            except (TypeError, ValueError):
                size = 0
            attachments.append(
                ThreadAttachment(
                    id=f"{message.message_id}:{attachment_id}",
                    filename=filename or f"attachment-{index + 1}",
                    mime_type=str(descriptor.get("mime_type") or "application/octet-stream"),
                    size=size,
                    attachment_id=attachment_id,
                    part_id=str(descriptor.get("part_id") or index),
                    download_url=(
                        f"/v1/mailbox/messages/{quote(message.message_id, safe='')}"
                        f"/attachments/{quote(attachment_id, safe='')}"
                    ),
                )
            )
        return attachments

    payload = message.raw_payload.get("payload") if isinstance(message.raw_payload, dict) else None
    if not isinstance(payload, dict):
        return []

    attachments: list[ThreadAttachment] = []
    for part_index, part in enumerate(_iter_gmail_payload_parts(payload)):
        body = part.get("body") if isinstance(part.get("body"), dict) else {}
        attachment_id = body.get("attachmentId") if isinstance(body, dict) else None
        if not isinstance(attachment_id, str) or not attachment_id:
            continue

        headers = _gmail_part_headers(part)
        disposition = headers.get("content-disposition", "").lower()
        filename = str(part.get("filename") or "").strip()
        if disposition.startswith("inline") or (not filename and headers.get("content-id")):
            continue
        if not filename and "attachment" not in disposition:
            continue

        part_id = str(part.get("partId") or part_index)
        mime_type = str(part.get("mimeType") or "").strip() or None
        size_value = body.get("size") if isinstance(body, dict) else None
        size = int(size_value) if isinstance(size_value, int) else None
        display_filename = filename or f"attachment-{len(attachments) + 1}"
        attachments.append(
            ThreadAttachment(
                id=f"{message.message_id}:{attachment_id}",
                filename=display_filename,
                mime_type=mime_type,
                size=size,
                attachment_id=attachment_id,
                part_id=part_id,
                download_url=(
                    f"/v1/mailbox/messages/{quote(message.message_id, safe='')}"
                    f"/attachments/{quote(attachment_id, safe='')}"
                ),
            )
        )
    return attachments


def _iter_gmail_payload_parts(part: dict[str, Any]):
    yield part
    children = part.get("parts")
    if not isinstance(children, list):
        return
    for child in children:
        if isinstance(child, dict):
            yield from _iter_gmail_payload_parts(child)


def _gmail_part_headers(part: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    raw_headers = part.get("headers")
    if not isinstance(raw_headers, list):
        return headers
    for header in raw_headers:
        if not isinstance(header, dict):
            continue
        name = str(header.get("name") or "").strip().lower()
        value = str(header.get("value") or "").strip()
        if name and value:
            headers[name] = value
    return headers


def _thread_message_from_gmail(
    message: GmailMessageRecord,
    *,
    settings: Settings | None = None,
    sender_avatar_asset_id: str | None = None,
) -> ThreadMessage:
    reader_html_body = html_body_for_reader(message.html_body_sanitized)
    reader_html_render_document = html_render_document_for_reader(message.html_render_document)
    html_body = (
        rewrite_external_image_sources(
            settings,
            user_id=message.user_id,
            message_id=message.message_id,
            document=reader_html_body,
        )
        if settings is not None and getattr(settings, "app_encryption_key", None)
        else reader_html_body
    )
    html_render_document = (
        rewrite_external_image_sources(
            settings,
            user_id=message.user_id,
            message_id=message.message_id,
            document=reader_html_render_document,
        )
        if settings is not None and getattr(settings, "app_encryption_key", None)
        else reader_html_render_document
    )
    return ThreadMessage(
        id=message.message_id,
        source="gmail",
        thread_id=message.gmail_thread_id,
        from_address=message.sender,
        sender_avatar_asset_id=sender_avatar_asset_id,
        reply_to=str(message.headers.get("reply-to") or message.headers.get("Reply-To") or "").strip() or None,
        to=message.recipients.get("to") if isinstance(message.recipients.get("to"), str) else None,
        cc=message.recipients.get("cc") if isinstance(message.recipients.get("cc"), str) else None,
        bcc=message.recipients.get("bcc") if isinstance(message.recipients.get("bcc"), str) else None,
        subject=message.subject,
        ai_title=message.ai_title,
        body=message.text_body or message.snippet or "",
        body_complete=not _needs_body_fetch(message),
        html_body=html_body,
        html_render_document=html_render_document,
        reader=build_thread_message_reader(
            html_render_document=reader_html_render_document,
            html_body=reader_html_body,
            text_body=message.text_body,
            snippet=message.snippet,
            headers=message.headers,
        ),
        snippet=message.snippet,
        attachments=gmail_attachments_for_message(message),
        label_ids=message.label_ids,
        received_at=message.internal_date or message.updated_at,
    )


def _thread_messages_from_gmail(
    messages: list[GmailMessageRecord],
    *,
    settings: Settings,
    user_id: str,
) -> list[ThreadMessage]:
    try:
        avatar_assets = resolve_contact_avatar_asset_ids(
            settings,
            user_id=user_id,
            sender_values=[message.sender for message in messages],
        )
    except Exception:
        logger.info("Contact avatar enrichment unavailable for user_id=%s", user_id, exc_info=True)
        avatar_assets = {}
    return [
        _thread_message_from_gmail(
            message,
            settings=settings,
            sender_avatar_asset_id=avatar_assets.get(normalize_contact_email(message.sender) or ""),
        )
        for message in messages
    ]


def _rebuild_missing_render_documents(settings: Settings, *, user_id: str, messages: list[GmailMessageRecord]) -> bool:
    records: list[GmailMessageRecord] = []
    for message in messages:
        if message.html_render_document or not message.raw_payload:
            continue
        parsed = parse_gmail_message(message.raw_payload, user_id=user_id)
        if parsed.get("html_render_document"):
            records.append(GmailMessageRecord(created_at="", updated_at="", **parsed))
    if not records:
        return False
    upsert_gmail_messages(str(settings.database_path), records)
    return True


def _needs_body_fetch(message: GmailMessageRecord) -> bool:
    if (message.body_fetch_status or "").lower() in {"fetched", "unavailable"}:
        return False
    return not has_persisted_renderable_body(
        text_body=message.text_body,
        html_body=message.html_body_sanitized,
        html_render_document=message.html_render_document,
        raw_payload=message.raw_payload,
    )


def _gmail_thread_body_ready(messages: list[GmailMessageRecord]) -> bool:
    return bool(messages) and all(not _needs_body_fetch(message) for message in messages)


def _gmail_thread_content_revision(messages: list[GmailMessageRecord]) -> str:
    """Return the same page-independent revision used by the DB reader query.

    ``content_revision`` is the per-message reader-visible change clock. The
    length prefix makes the ordered concatenation unambiguous without moving
    bodies or attachment payloads through the API merely to calculate a thread
    cache key. MD5 is used only as a compact opaque change token, never for a
    security decision.
    """

    serialized = "".join(
        f"{len(message.message_id)}:{message.message_id}:{max(1, int(message.content_revision or 1))}"
        for message in sorted(messages, key=lambda item: item.message_id)
    )
    return hashlib.md5(serialized.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]


def _enqueue_reader_body_fetch_best_effort(
    settings: Settings,
    *,
    user_id: str,
    group_id: str = "",
    gmail_thread_id: str = "",
    priority: int,
) -> None:
    """Queue Gmail network work without making a reader request wait for it."""
    try:
        if gmail_thread_id:
            _enqueue_body_fetch_for_gmail_thread(
                settings,
                user_id=user_id,
                gmail_thread_id=gmail_thread_id,
                priority=priority,
            )
        elif group_id:
            _enqueue_body_fetch_for_group(
                settings,
                user_id=user_id,
                group_id=group_id,
                priority=priority,
            )
    except Exception as exc:  # pragma: no cover - the cached reader must remain available.
        logger.warning(
            "Failed to enqueue reader body hydration user_id=%s thread_id=%s error=%s",
            user_id,
            gmail_thread_id or group_id,
            type(exc).__name__,
        )


def _enqueue_body_fetch_for_group(settings: Settings, *, user_id: str, group_id: str, priority: int) -> str:
    job = enqueue_job(
        str(settings.database_path),
        kind="gmail_body_fetch",
        queue="reader",
        user_id=user_id,
        dedupe_key=f"gmail-body-fetch:{user_id}:{group_id}",
        priority=priority,
        payload={"user_id": user_id, "group_id": group_id},
        wake_existing=priority >= 100,
    )
    return job.id


def _enqueue_body_fetch_for_gmail_thread(settings: Settings, *, user_id: str, gmail_thread_id: str, priority: int) -> str:
    job = enqueue_job(
        str(settings.database_path),
        kind="gmail_body_fetch",
        queue="reader",
        user_id=user_id,
        dedupe_key=f"gmail-body-fetch-thread:{user_id}:{gmail_thread_id}",
        priority=priority,
        payload={"user_id": user_id, "gmail_thread_id": gmail_thread_id},
        wake_existing=priority >= 100,
    )
    return job.id


def _bucket_rows(rows: list[GmailThreadRow]) -> list[GmailThreadSection]:
    buckets: OrderedDict[str, tuple[str, list[GmailThreadRow]]] = OrderedDict()
    for row in rows:
        key, title = _mailbox_row_date_bucket(row)
        if key not in buckets:
            buckets[key] = (title, [])
        buckets[key][1].append(row)
    return [GmailThreadSection(id=key, title=title, rows=items) for key, (title, items) in buckets.items()]


def _bucket_rows_in_order(rows: list[GmailThreadRow]) -> list[GmailThreadSection]:
    """Keep Gmail rank exact even when its date buckets are non-contiguous."""
    sections: list[GmailThreadSection] = []
    current_bucket_key: str | None = None
    for row in rows:
        bucket_key, title = _mailbox_row_date_bucket(row)
        if bucket_key != current_bucket_key:
            sections.append(
                GmailThreadSection(
                    id=f"{bucket_key}:gmail-order:{row.thread_id}",
                    title=title,
                    rows=[],
                )
            )
            current_bucket_key = bucket_key
        sections[-1].rows.append(row)
    return sections


def _mailbox_row_date_bucket(row: GmailThreadRow) -> tuple[str, str]:
    reference = datetime.now().astimezone().date()
    date = _parse_date(row.latest_received_at).date()
    age = (reference - date).days
    if age <= 0:
        return "today", "Today"
    if age == 1:
        return "yesterday", "Yesterday"
    if age <= 7:
        return "last-seven-days", "Last seven days"
    return f"month-{date.year}-{date.month}", date.strftime("%B %Y")


def _parse_date(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now().astimezone()
    return parsed.astimezone() if parsed.tzinfo else parsed.astimezone()


def _mailbox_label(label: str) -> MailboxLabel:
    normalized = label.strip().lower()
    return normalized if normalized in {"inbox", "sent", "drafts", "spam", "trash", "archive", "starred", "all"} else "inbox"  # type: ignore[return-value]


def _group_matches_label(group: MailGroupRecord, label: str) -> bool:
    labels = {item.upper() for item in group.labels}
    if label == "all":
        return True
    if label == "inbox":
        return "INBOX" in labels or not ({"SENT", "TRASH", "DRAFT"} & labels)
    if label == "sent":
        return "SENT" in labels
    if label == "trash":
        return "TRASH" in labels
    if label == "drafts":
        return "DRAFT" in labels
    if label == "spam":
        return "SPAM" in labels
    if label == "starred":
        return "STARRED" in labels
    if label == "archive":
        return not ({"INBOX", "SENT", "DRAFT", "SPAM", "TRASH"} & labels)
    return True
