from __future__ import annotations

"""Mail group product pipeline: grouping, enrichment, dashboard mapping."""

from collections import defaultdict, OrderedDict
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
import hashlib
import json
import re
from typing import Any

from app.core.config import Settings
from app.db.jobs import count_active_jobs, enqueue_job
from app.db.mail_groups import (
    EntityOutcomeRecord,
    GmailMessageRecord,
    MailGroupRecord,
    ManualTaskRecord,
    append_group_members,
    count_mail_groups,
    count_mail_groups_by_enrichment_status,
    count_dashboard_mail_groups,
    count_ready_mail_groups_since,
    get_app_session_snapshot,
    get_import_state,
    get_latest_entity_outcomes,
    get_mail_group_by_key,
    get_mail_group_detail,
    list_dashboard_mail_groups,
    list_group_messages,
    list_mail_groups,
    list_messages_by_ids,
    list_messages_for_groups,
    list_open_manual_tasks,
    list_pending_mail_groups,
    list_recent_messages,
    list_recent_messages_since,
    mark_import_completed,
    mark_import_error,
    oldest_imported_message_at,
    replace_group_members,
    upsert_app_session_snapshot,
    upsert_gmail_messages,
    upsert_mail_group,
    user_can_write_gmail,
)
from app.schemas.domain import (
    AppSessionResponse,
    AppSessionSyncState,
    AppSessionUser,
    AttentionItem,
    AttentionItemDetail,
    AuthUserResponse,
    DashboardBriefing,
    DashboardProfile,
    DashboardResponse,
    FeedResponse,
    GmailThreadRow,
    GmailThreadSection,
    GoogleAuthState,
    MailboxLabel,
    MailboxResponse,
    MailboxSyncStateResponse,
    PostLoginReadinessResponse,
    ThreadMessage,
    ThreadReaderResponse,
)
from app.services.email_extraction import (
    build_thread_message_reader,
    clean_ai_text,
    compact_text,
    has_persisted_renderable_body,
    html_body_for_reader,
    html_render_document_for_reader,
    parse_gmail_message,
    sender_domain,
)

FIRST_BATCH_SIZE = 50
BACKFILL_BATCH_SIZE = 30
MAILBOX_REBUILD_LIMIT = 5000
MAIL_GROUP_ENRICH_BATCH_SIZE = 10
FIRST_RUN_AI_MAX_GROUPS = 12
FIRST_RUN_AI_CANDIDATE_LIMIT = 25
DASHBOARD_DAYS = 14
RECENT_VISIBLE_DAYS = 30
ACTION_WORD_RE = re.compile(r"(?i)\b(reply|respond|confirm|approve|review|pay|payment failed|due|overdue|urgent|action required|scheduled|interview|check.?in|delivered|out for delivery|ticket|case|invoice|receipt)\b")
LIFECYCLE_WORD_RE = re.compile(r"(?i)\b(order|shipped|delivered|tracking|invoice|payment|booking|flight|application|ticket|case|request|approved|received|account|created|profile|welcome)\b")
ALLOWED_ACTION_TYPES = {"pay", "reply", "confirm", "track", "review", "open", "none"}
ALLOWED_TIMING_BANDS = {"now", "today", "later", "hidden"}
GENERIC_SENDER_SLUGS = {"alert", "alerts", "info", "mail", "no-reply", "noreply", "notification", "notifications", "support", "team"}
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


def enqueue_first_run(settings: Settings, *, user_id: str) -> str:
    job = enqueue_job(
        str(settings.database_path),
        kind="gmail_import_batch",
        queue="critical",
        user_id=user_id,
        dedupe_key=f"first-run:{user_id}",
        priority=100,
        payload={"user_id": user_id, "batch_size": FIRST_BATCH_SIZE, "first_run": True},
    )
    return job.id


def enqueue_mailbox_sync(settings: Settings, *, user_id: str) -> str:
    state = get_import_state(str(settings.database_path), user_id=user_id)
    if state is not None and state.last_history_id:
        job = enqueue_job(
            str(settings.database_path),
            kind="gmail_delta_sync",
            queue="critical",
            user_id=user_id,
            dedupe_key=f"gmail-delta-sync:{user_id}",
            priority=85,
            payload={"user_id": user_id, "batch_size": 100, "source": "manual"},
        )
        ensure_background_import_work(settings, user_id=user_id)
        return job.id

    job = enqueue_job(
        str(settings.database_path),
        kind="gmail_import_batch",
        queue="default",
        user_id=user_id,
        dedupe_key=f"mailbox-sync:{user_id}",
        priority=20,
        payload={"user_id": user_id, "batch_size": BACKFILL_BATCH_SIZE, "first_run": False},
    )
    ensure_background_import_work(settings, user_id=user_id)
    return job.id


def ensure_background_import_work(settings: Settings, *, user_id: str) -> None:
    database_url = str(settings.database_path)
    if not user_can_write_gmail(database_url, user_id=user_id):
        return
    state = get_import_state(database_url, user_id=user_id)
    active_backfills = count_active_jobs(database_url, user_id=user_id, kinds=["gmail_backfill"])
    if state is not None and state.full_backfill_cursor and not active_backfills:
        enqueue_job(
            database_url,
            kind="gmail_backfill",
            queue="slow",
            user_id=user_id,
            dedupe_key=f"gmail-backfill:{user_id}:resume",
            priority=80,
            payload={"user_id": user_id, "batch_size": BACKFILL_BATCH_SIZE},
        )
    status_counts = count_mail_groups_by_enrichment_status(database_url, user_id=user_id)
    if status_counts.get("pending", 0) > 0:
        enqueue_job(
            database_url,
            kind="mail_group_enrich",
            queue="default",
            user_id=user_id,
            dedupe_key=f"mail-group-enrich:{user_id}",
            priority=40,
            payload={"user_id": user_id},
        )


def build_app_session_response(settings: Settings, *, user) -> AppSessionResponse:
    """Read the prepared app-session snapshot shared by dashboard and Gmail."""
    database_url = str(settings.database_path)
    auth = GoogleAuthState(available=settings.google_configured, connected=user_can_write_gmail(database_url, user_id=user.id))
    snapshot = get_app_session_snapshot(database_url, user_id=user.id) if auth.connected else None
    if snapshot is None:
        return _empty_app_session_response(settings, user=user, auth=auth)
    dashboard = DashboardResponse.model_validate(snapshot.dashboard)
    mailbox = MailboxResponse.model_validate(snapshot.mailbox)
    sync = AppSessionSyncState.model_validate(snapshot.sync)
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
    dashboard = DashboardResponse(auth=auth, profile=user.profile, feed=FeedResponse())
    mailbox = MailboxResponse(label="inbox", total_threads=0)
    sync = AppSessionSyncState(
        last_sync_at=None,
        last_error=None,
        enrichment_pending_count=0,
        ready_group_count=0,
        oldest_imported_at=None,
        full_import_running=False,
        full_import_completed=False,
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
    has_prior_product = bool((state and state.first_groups_ready_at) or sync.ready_group_count)
    mode = "returning" if has_prior_product else "first_time"
    mailbox_ready = mailbox.total_threads > 0 or sync.ready_group_count > 0
    dashboard_ready = bool((state and state.first_dashboard_ready_at) or sync.ready_group_count > 0)
    if mode == "returning":
        ready_to_enter = mailbox_ready and dashboard_ready
    else:
        ready_to_enter = bool(
            state
            and state.first_batch_imported_at
            and state.first_groups_ready_at
            and state.first_dashboard_ready_at
            and mailbox_ready
        )
    stage = _post_login_stage(
        mode=mode,
        ready_to_enter=ready_to_enter,
        state=state,
        active_setup_jobs=0,
        mailbox_ready=mailbox_ready,
        dashboard_ready=dashboard_ready,
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
    )


def refresh_app_session_snapshot(settings: Settings, *, user_id: str) -> AppSessionResponse | None:
    from app.db.repository import get_user
    from app.services.auth import CurrentUser

    stored = get_user(str(settings.database_path), user_id)
    if stored is None:
        return None
    user = CurrentUser(id=stored.id, email=stored.email, display_name=stored.display_name)
    database_url = str(settings.database_path)
    auth = GoogleAuthState(available=settings.google_configured, connected=user_can_write_gmail(database_url, user_id=user.id))
    dashboard = build_dashboard_response(settings, user_id=user.id, auth=auth, profile=user.profile)
    mailbox = build_mailbox_response(settings, user_id=user.id, label="inbox", limit=1000)
    status_counts = count_mail_groups_by_enrichment_status(database_url, user_id=user.id)
    state = get_import_state(database_url, user_id=user.id)
    sync = AppSessionSyncState(
        last_sync_at=state.last_import_completed_at if state else None,
        last_error=state.last_sync_error if state else None,
        enrichment_pending_count=status_counts.get("pending", 0),
        ready_group_count=status_counts.get("ready", 0),
        oldest_imported_at=oldest_imported_message_at(database_url, user_id=user.id),
        full_import_running=mailbox.full_import_running,
        full_import_completed=mailbox.full_import_completed,
    )
    upsert_app_session_snapshot(
        database_url,
        user_id=user.id,
        dashboard=dashboard.model_dump(mode="json"),
        mailbox=mailbox.model_dump(mode="json"),
        sync=sync.model_dump(mode="json"),
    )
    return build_app_session_response(settings, user=user)


def enqueue_projection_refresh(settings: Settings, *, user_id: str, priority: int = 15) -> str:
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


def build_post_login_readiness_response(
    settings: Settings,
    *,
    user,
    dashboard: DashboardResponse | None = None,
    mailbox: MailboxResponse | None = None,
) -> PostLoginReadinessResponse:
    database_url = str(settings.database_path)
    state = get_import_state(database_url, user_id=user.id)
    recent_since = (datetime.now(timezone.utc) - timedelta(days=RECENT_VISIBLE_DAYS)).isoformat()
    dashboard_since = (datetime.now(timezone.utc) - timedelta(days=DASHBOARD_DAYS)).isoformat()
    ready_mail_group_count = count_mail_groups(database_url, user_id=user.id)
    recent_ready_count = count_ready_mail_groups_since(database_url, user_id=user.id, since_iso=recent_since)
    ready_dashboard_count = (
        sum(len(section) for section in [dashboard.feed.now, dashboard.feed.today, dashboard.feed.worth_knowing])
        if dashboard is not None
        else count_dashboard_mail_groups(database_url, user_id=user.id, since_iso=dashboard_since)
    )
    active_backfill_jobs = count_active_jobs(database_url, user_id=user.id, kinds=["gmail_backfill"])
    active_setup_jobs = count_active_jobs(
        database_url,
        user_id=user.id,
        kinds=["gmail_import_batch", "first_run_ai_grouping", "mail_group_enrich"],
    )
    full_import_running = bool(active_backfill_jobs) or bool(state and state.full_backfill_cursor)
    full_import_completed = bool(state and state.first_batch_imported_at and not full_import_running)
    has_prior_product = bool((state and state.first_groups_ready_at) or ready_mail_group_count)
    mode = "returning" if has_prior_product else "first_time"
    mailbox_ready = (mailbox.total_threads > 0 if mailbox is not None else recent_ready_count > 0) or ready_mail_group_count > 0
    dashboard_ready = bool((state and state.first_dashboard_ready_at) or ready_mail_group_count > 0)
    if mode == "returning":
        ready_to_enter = mailbox_ready and dashboard_ready
    else:
        ready_to_enter = bool(
            state
            and state.first_batch_imported_at
            and state.first_groups_ready_at
            and state.first_dashboard_ready_at
            and recent_ready_count > 0
        )
    stage = _post_login_stage(
        mode=mode,
        ready_to_enter=ready_to_enter,
        state=state,
        active_setup_jobs=active_setup_jobs,
        mailbox_ready=mailbox_ready,
        dashboard_ready=dashboard_ready,
    )
    display_name = _first_name(user.profile) or user.profile.display_name or user.email
    return PostLoginReadinessResponse(
        mode=mode,  # type: ignore[arg-type]
        stage=stage,  # type: ignore[arg-type]
        ready_to_enter=ready_to_enter,
        dashboard_ready=dashboard_ready,
        mailbox_ready=mailbox_ready,
        ready_dashboard_count=ready_dashboard_count,
        ready_mail_group_count=ready_mail_group_count,
        full_import_running=full_import_running,
        full_import_completed=full_import_completed,
        user_display_name=display_name,
        error_message=state.last_sync_error if state and state.last_sync_error and not ready_to_enter else None,
    )


def _post_login_stage(
    *,
    mode: str,
    ready_to_enter: bool,
    state,
    active_setup_jobs: int,
    mailbox_ready: bool,
    dashboard_ready: bool,
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
        return "grouping_threads"
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
    use_ai: bool = True,
    max_ai_groups: int | None = None,
) -> int:
    database_url = str(settings.database_path)
    if not user_can_write_gmail(database_url, user_id=user_id):
        return 0
    messages = list_recent_messages(database_url, user_id=user_id, limit=limit)
    candidates = _candidate_groups(messages)
    touched = 0
    ai_groups_processed = 0
    for group_key, grouped_messages in candidates.items():
        latest = max(grouped_messages, key=lambda item: item.internal_date or item.updated_at)
        group_hash = _group_hash(grouped_messages)
        existing = get_mail_group_by_key(database_url, user_id=user_id, group_key=group_key)
        if use_ai and existing is not None and existing.generated_from_hash == group_hash and existing.generated_at is not None:
            continue
        if use_ai and max_ai_groups is not None and ai_groups_processed >= max_ai_groups:
            continue
        if not use_ai and existing is not None and existing.generated_from_hash == group_hash and existing.generated_at is not None:
            enrichment = _enrichment_from_existing(existing)
            enrichment["_ai_ready"] = existing.enrichment_status == "ready"
            generated_at = existing.generated_at
        else:
            enrichment = enrich_group(
                settings,
                messages=grouped_messages,
                group_key=group_key,
                generated_from_hash=group_hash,
                use_ai=use_ai,
            )
            generated_at = datetime.now(timezone.utc).isoformat() if enrichment.get("_ai_ready") else None
            if use_ai:
                ai_groups_processed += 1
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
            ai_model=settings.openai_model if ai_ready else None,
            ai_error=str(enrichment.get("_ai_error"))[:1000] if enrichment.get("_ai_error") else None,
            ai_generated_at=generated_at,
        )
        replace_group_members(
            database_url,
            user_id=user_id,
            group_id=group.id,
            members=[(message, _member_reason(message), 0.9) for message in grouped_messages],
        )
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
    database_url = str(settings.database_path)
    if not user_can_write_gmail(database_url, user_id=user_id):
        return 0
    messages = list_messages_by_ids(database_url, user_id=user_id, message_ids=message_ids)
    candidates = _candidate_groups(messages)
    touched = 0
    ai_groups_processed = 0
    for group_key, grouped_messages in candidates.items():
        existing = get_mail_group_by_key(database_url, user_id=user_id, group_key=group_key)
        candidate_messages = _merge_group_messages(
            [*(list_group_messages(database_url, user_id=user_id, group_id=existing.id) if existing is not None else []), *grouped_messages]
        )
        latest = max(candidate_messages, key=lambda item: item.internal_date or item.updated_at)
        group_hash = _group_hash(candidate_messages)
        if use_ai and max_ai_groups is not None and ai_groups_processed >= max_ai_groups:
            use_ai_for_group = False
        else:
            use_ai_for_group = use_ai

        if (
            existing is not None
            and existing.enrichment_status == "ready"
            and existing.generated_from_hash == group_hash
            and existing.generated_at is not None
            and not use_ai_for_group
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
                use_ai=use_ai_for_group,
            )
            generated_at = datetime.now(timezone.utc).isoformat() if enrichment.get("_ai_ready") else None
            membership_source = _member_reason(grouped_messages[0])
            ai_model = settings.openai_model if enrichment.get("_ai_ready") else None
            ai_error = str(enrichment.get("_ai_error"))[:1000] if enrichment.get("_ai_error") else None
            ai_generated_at = generated_at
            if use_ai_for_group:
                ai_groups_processed += 1

        ai_ready = bool(enrichment.get("_ai_ready"))
        generated_at = generated_at or (datetime.now(timezone.utc).isoformat() if fallback_ready and not use_ai_for_group else None)
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
        )
        replace_group_members(
            database_url,
            user_id=user_id,
            group_id=group.id,
            members=[(message, _member_reason(message), 0.9) for message in candidate_messages],
        )
        touched += 1
    return touched


def enrich_pending_mail_groups(settings: Settings, *, user_id: str, limit: int = MAIL_GROUP_ENRICH_BATCH_SIZE) -> int:
    database_url = str(settings.database_path)
    if not user_can_write_gmail(database_url, user_id=user_id):
        return 0
    groups = list_pending_mail_groups(database_url, user_id=user_id, limit=limit)
    touched = 0
    for group in groups:
        messages = list_group_messages(database_url, user_id=user_id, group_id=group.id)
        if not messages:
            continue
        latest = max(messages, key=lambda item: item.internal_date or item.updated_at)
        group_hash = _group_hash(messages)
        enrichment = enrich_group(
            settings,
            messages=messages,
            group_key=group.group_key,
            generated_from_hash=group_hash,
            use_ai=True,
        )
        ai_ready = bool(enrichment.get("_ai_ready"))
        upsert_mail_group(
            database_url,
            user_id=user_id,
            group_key=group.group_key,
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
            generated_at=datetime.now(timezone.utc).isoformat() if ai_ready else None,
            enrichment_status="ready" if ai_ready else "pending",
            membership_source=group.membership_source,
            ai_model=settings.openai_model if ai_ready else None,
            ai_error=str(enrichment.get("_ai_error"))[:1000] if enrichment.get("_ai_error") else None,
            ai_generated_at=datetime.now(timezone.utc).isoformat() if ai_ready else None,
        )
        replace_group_members(
            database_url,
            user_id=user_id,
            group_id=group.id,
            members=[(message, group.membership_source, 0.9) for message in messages],
        )
        touched += 1
    return touched


def run_first_run_ai_grouping(settings: Settings, *, user_id: str, limit: int = FIRST_BATCH_SIZE) -> int:
    database_url = str(settings.database_path)
    if not user_can_write_gmail(database_url, user_id=user_id):
        mark_import_error(database_url, user_id=user_id, error="Gmail is disconnected or data deletion is active.")
        raise RuntimeError("Gmail is disconnected or data deletion is active.")
    recent_since = (datetime.now(timezone.utc) - timedelta(days=RECENT_VISIBLE_DAYS)).isoformat()
    all_messages = list_recent_messages_since(database_url, user_id=user_id, since_iso=recent_since, limit=max(limit, FIRST_BATCH_SIZE))
    if not all_messages:
        all_messages = list_recent_messages(database_url, user_id=user_id, limit=limit)
    candidate_messages = _first_run_candidate_messages(all_messages)
    if not candidate_messages:
        mark_import_error(database_url, user_id=user_id, error="No Gmail messages were imported for first-run grouping.")
        raise RuntimeError("No Gmail messages were imported for first-run grouping.")
    try:
        grouped_outputs = _ai_batch_group_messages(settings, messages=candidate_messages)
        created, visible_created, used_message_ids = _store_ai_batch_groups(
            settings,
            user_id=user_id,
            messages=candidate_messages,
            grouped_outputs=grouped_outputs,
        )
        if created < 3 and visible_created < 1:
            raise RuntimeError("AI grouping returned insufficient product-quality groups.")
        ungrouped_message_ids = [message.message_id for message in all_messages if message.message_id not in used_message_ids]
        if ungrouped_message_ids:
            rebuild_touched_mail_groups(settings, user_id=user_id, message_ids=ungrouped_message_ids, use_ai=False)
            enqueue_job(
                database_url,
                kind="mail_group_enrich",
                queue="default",
                user_id=user_id,
                dedupe_key=f"mail-group-enrich:{user_id}",
                priority=55,
                payload={"user_id": user_id},
            )
        mark_import_completed(database_url, user_id=user_id, groups_ready=True, dashboard_ready=True)
        refresh_app_session_snapshot(settings, user_id=user_id)
        return created
    except Exception as exc:
        mark_import_error(database_url, user_id=user_id, error=f"first_run_ai_grouping failed: {exc}")
        raise


def build_dashboard_response(settings: Settings, *, user_id: str | None, auth: GoogleAuthState, profile: DashboardProfile | None = None) -> DashboardResponse:
    if user_id is None or not auth.connected:
        return DashboardResponse(auth=auth, feed=FeedResponse())
    since = (datetime.now(timezone.utc) - timedelta(days=DASHBOARD_DAYS)).isoformat()
    database_url = str(settings.database_path)
    groups = list_dashboard_mail_groups(database_url, user_id=user_id, since_iso=since)
    manual_tasks = list_open_manual_tasks(database_url, user_id=user_id)
    outcomes = get_latest_entity_outcomes(
        database_url,
        user_id=user_id,
        entity_ids=[*[group.id for group in groups], *[task.entity_id for task in manual_tasks]],
    )
    groups = [group for group in groups if not _outcome_suppresses(outcomes.get(group.id))]
    manual_tasks = [task for task in manual_tasks if not _outcome_suppresses(outcomes.get(task.entity_id))]
    group_messages = list_messages_for_groups(database_url, user_id=user_id, group_ids=[group.id for group in groups])
    groups = _dedupe_groups_by_messages(groups, group_messages)
    status_counts = count_mail_groups_by_enrichment_status(database_url, user_id=user_id)
    feed = FeedResponse()
    for group in groups:
        _append_feed_item(feed, _attention_item_from_group(group))
    for task in manual_tasks:
        _append_feed_item(feed, _attention_item_from_manual_task(task))
    briefing = _dashboard_briefing(profile=profile, feed=feed)
    return DashboardResponse(
        auth=auth,
        profile=profile,
        briefing=briefing,
        feed=feed,
        runtime_status={
            "feed_source": "ai_mail_groups" if groups else "manual_tasks" if manual_tasks else "empty",
            "canonical_ready": bool(status_counts.get("ready", 0)),
            "ai_groups_ready": bool(status_counts.get("ready", 0)),
            "pending_group_count": status_counts.get("pending", 0),
            "ready_group_count": status_counts.get("ready", 0),
            "last_mailbox_sync_at": None,
            "queue_lag_seconds": None,
            "stale_reason": None,
        },
    )


def _first_run_candidate_messages(messages: list[GmailMessageRecord]) -> list[GmailMessageRecord]:
    return sorted(messages, key=_first_run_message_score, reverse=True)[:FIRST_RUN_AI_CANDIDATE_LIMIT]


def _first_run_message_score(message: GmailMessageRecord) -> tuple[int, str]:
    labels = {label.upper() for label in message.label_ids}
    text = " ".join([message.subject or "", message.sender or "", message.snippet or "", message.text_body or ""])
    signals = message.extracted_signals
    score = 0
    if "UNREAD" in labels:
        score += 50
    if "INBOX" in labels:
        score += 30
    if ACTION_WORD_RE.search(text):
        score += 40
    if LIFECYCLE_WORD_RE.search(text):
        score += 25
    for signal_name in ["order_id", "ticket_id", "tracking_id", "invoice_id", "booking_id", "application_id"]:
        if signals.get(signal_name):
            score += 35
            break
    if signals.get("list_id"):
        score -= 20
    if "CATEGORY_PROMOTIONS" in labels or "CATEGORY_SOCIAL" in labels:
        score -= 30
    return (score, message.internal_date or message.updated_at)


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


def build_mailbox_response(settings: Settings, *, user_id: str, label: str = "inbox", limit: int = 150, cursor: str | None = None) -> MailboxResponse:
    database_url = str(settings.database_path)
    state = get_import_state(database_url, user_id=user_id)
    full_import_running = bool(state and state.full_backfill_cursor)
    groups = list_mail_groups(database_url, user_id=user_id, limit=limit, include_pending=True)
    outcomes = get_latest_entity_outcomes(database_url, user_id=user_id, entity_ids=[group.id for group in groups])
    groups = [group for group in groups if not _outcome_suppresses(outcomes.get(group.id))]
    group_messages = list_messages_for_groups(database_url, user_id=user_id, group_ids=[group.id for group in groups])
    groups = _dedupe_groups_by_messages(groups, group_messages)
    status_counts = count_mail_groups_by_enrichment_status(database_url, user_id=user_id)
    mailbox_label = _mailbox_label(label)
    rows = [
        _gmail_row_from_group(group, group_messages.get(group.id, []))
        for group in groups
        if _group_matches_label(group, mailbox_label)
    ]
    return MailboxResponse(
        label=mailbox_label,
        total_threads=len(rows),
        next_cursor=None,
        sections=_bucket_rows(rows),
        ready_count=status_counts.get("ready", 0),
        pending_count=status_counts.get("pending", 0),
        oldest_imported_at=oldest_imported_message_at(database_url, user_id=user_id),
        full_import_running=full_import_running,
        full_import_completed=bool(state and state.first_batch_imported_at and not full_import_running),
    )


def build_mailbox_sync_state(settings: Settings, *, user_id: str) -> MailboxSyncStateResponse:
    state = get_import_state(str(settings.database_path), user_id=user_id)
    connected = user_can_write_gmail(str(settings.database_path), user_id=user_id)
    return MailboxSyncStateResponse(
        connected=connected,
        last_history_id=state.last_history_id if state else None,
        last_full_sync_at=state.last_import_completed_at if state else None,
        watch_expiration_at=getattr(state, "gmail_watch_expiration_at", None) if state else None,
        last_sync_started_at=state.last_import_started_at if state else None,
        last_sync_completed_at=state.last_import_completed_at if state else None,
        last_sync_error=(state.last_sync_error or getattr(state, "gmail_watch_error", None)) if state else None,
        total_threads=count_mail_groups(str(settings.database_path), user_id=user_id),
    )


def build_group_detail_response(settings: Settings, *, user_id: str, group_id: str, limit: int = 50, offset: int = 0) -> ThreadReaderResponse | None:
    detail = get_mail_group_detail(str(settings.database_path), user_id=user_id, group_id=group_id)
    if detail is None:
        return None
    if _rebuild_missing_render_documents(settings, user_id=user_id, messages=detail.messages):
        detail = get_mail_group_detail(str(settings.database_path), user_id=user_id, group_id=group_id) or detail
    if any(_needs_body_fetch(message) for message in detail.messages):
        try:
            from app.services.gmail_importer import run_gmail_body_fetch

            if run_gmail_body_fetch(settings, user_id=user_id, group_id=group_id):
                detail = get_mail_group_detail(str(settings.database_path), user_id=user_id, group_id=group_id) or detail
        except Exception:
            enqueue_job(
                str(settings.database_path),
                kind="gmail_body_fetch",
                queue="default",
                user_id=user_id,
                dedupe_key=f"gmail-body-fetch:{user_id}:{group_id}",
                priority=30,
                payload={"user_id": user_id, "group_id": group_id},
            )
    messages = detail.messages[offset : offset + limit]
    return ThreadReaderResponse(
        entity_id=detail.group.id,
        user_id=user_id,
        source="gmail",
        gmail_thread_id=detail.group.id,
        subject=detail.group.ai_title,
        total_messages=len(detail.messages),
        limit=limit,
        offset=offset,
        has_more=offset + len(messages) < len(detail.messages),
        messages=[_thread_message_from_gmail(message) for message in messages],
    )


def enrich_group(
    settings: Settings,
    *,
    messages: list[GmailMessageRecord],
    group_key: str,
    generated_from_hash: str,
    use_ai: bool = True,
) -> dict[str, Any]:
    fallback = _fallback_enrichment(messages, group_key)
    if not use_ai or not settings.openai_configured:
        return fallback
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=30.0)
        prompt = {
            "task": "Turn candidate Gmail messages into one real-life mail group.",
            "rules": [
                "Use the candidate only if the messages represent the same real-life thing.",
                "Do not mark newsletters, event ads, or marketing as action_needed unless the user clearly must act.",
                "Do not invent pay, reply, or confirm actions from boilerplate words.",
                "Return a concise human title, not an email subject with Re/Fwd prefixes.",
                "Return a summary that explains what happened and what the user should know.",
            ],
            "required_json_fields": ["group_type", "ai_title", "ai_summary", "labels", "action_needed", "action_type", "priority", "timing_band", "dashboard_visible"],
            "allowed_action_type": sorted(ALLOWED_ACTION_TYPES),
            "allowed_timing_band": sorted(ALLOWED_TIMING_BANDS),
            "messages": [
                {
                    "subject": message.subject,
                    "sender": message.sender,
                    "date": message.internal_date,
                    "snippet": message.snippet,
                    "text": (message.text_body or "")[:2500],
                    "signals": message.extracted_signals,
                }
                for message in messages[:12]
            ],
        }
        response = client.responses.create(
            model=settings.openai_model,
            input=[
                {"role": "system", "content": "Return strict JSON only. Be concise. Dashboard should show only actionable or very useful recent groups."},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=True)},
            ],
            text={"format": {"type": "json_object"}},
            store=False,
        )
        text_output = getattr(response, "output_text", "") or ""
        parsed = json.loads(text_output)
        enriched = _normalize_enrichment(parsed, fallback)
        enriched["labels"] = sorted(set(enriched["labels"]) | {label for message in messages for label in message.label_ids})
        enriched["_ai_ready"] = True
        return enriched
    except Exception as exc:
        fallback["_fallback"] = True
        fallback["_ai_error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        return fallback


def _ai_batch_group_messages(settings: Settings, *, messages: list[GmailMessageRecord]) -> list[dict[str, Any]]:
    if not settings.openai_configured:
        raise RuntimeError("OpenAI is not configured for first-run AI grouping.")
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key, timeout=45.0)
    prompt = {
        "task": "Create product-visible mail groups from recent Gmail messages.",
        "rules": [
            "You are creating product-visible mail groups, not summarizing individual emails.",
            "Group emails by the real-life thing they represent.",
            "Use Gmail threadId as one signal, not the final grouping.",
            "Do not mark newsletters, event ads, or marketing as action_needed unless the user clearly must act.",
            "Do not invent pay, reply, or confirm actions from boilerplate words.",
            "Return concise human titles, not email subjects with Re/Fwd prefixes.",
            "Return summaries that explain what happened and what the user should know.",
            "A message can appear in only one output group.",
            f"Return at most {FIRST_RUN_AI_MAX_GROUPS} groups for first paint.",
            "Prioritize unread, actionable, delivery, billing, travel, support, application, and account lifecycle groups.",
            "Omit low-signal marketing, newsletter, and routine notification groups from this first response.",
        ],
        "allowed_action_type": sorted(ALLOWED_ACTION_TYPES),
        "allowed_timing_band": sorted(ALLOWED_TIMING_BANDS),
        "dashboard_visibility_rule": "true only if action_needed is true or the recent awareness is genuinely important.",
        "max_groups": FIRST_RUN_AI_MAX_GROUPS,
        "candidate_context": _candidate_context_for_ai(messages),
        "messages": [_message_for_ai(message) for message in messages],
        "output_schema": {
            "groups": [
                {
                    "client_group_key": "stable short key",
                    "group_type": "conversation | support_case | order_lifecycle | billing | travel | application | newsletter | marketing | account_lifecycle | other",
                    "ai_title": "human product title",
                    "ai_summary": "one or two useful sentences",
                    "labels": ["short", "labels"],
                    "action_needed": False,
                    "action_type": "pay | reply | confirm | track | review | open | none",
                    "priority": 0,
                    "timing_band": "now | today | later | hidden",
                    "dashboard_visible": False,
                    "member_message_ids": ["gmail-message-id"],
                }
            ]
        },
    }
    response = client.responses.create(
        model=settings.openai_model,
        input=[
            {
                "role": "system",
                "content": (
                    "Return strict JSON only. Do not include markdown. "
                    "Every member_message_id must come from the provided input."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=True)},
        ],
        text={"format": {"type": "json_object"}},
        store=False,
    )
    parsed = json.loads(getattr(response, "output_text", "") or "{}")
    groups = parsed.get("groups") if isinstance(parsed, dict) else None
    if not isinstance(groups, list):
        raise RuntimeError("AI grouping response did not contain a groups array.")
    return [group for group in groups if isinstance(group, dict)]


def _store_ai_batch_groups(
    settings: Settings,
    *,
    user_id: str,
    messages: list[GmailMessageRecord],
    grouped_outputs: list[dict[str, Any]],
) -> tuple[int, int, set[str]]:
    database_url = str(settings.database_path)
    by_id = {message.message_id: message for message in messages}
    used_ids: set[str] = set()
    created = 0
    visible_created = 0
    for index, output in enumerate(grouped_outputs):
        member_ids = []
        raw_member_ids = output.get("member_message_ids", [])
        if not isinstance(raw_member_ids, list):
            continue
        for item in raw_member_ids:
            message_id = str(item)
            if message_id in by_id and message_id not in used_ids and message_id not in member_ids:
                member_ids.append(message_id)
        if not member_ids:
            continue
        title = compact_text(str(output.get("ai_title") or ""))
        summary = compact_text(str(output.get("ai_summary") or ""))
        if not title or not summary:
            continue
        used_ids.update(member_ids)
        members = [by_id[message_id] for message_id in member_ids]
        latest = max(members, key=lambda item: item.internal_date or item.updated_at)
        group_hash = _group_hash(members)
        group_key = _ai_group_key(output.get("client_group_key"), members, index)
        action_type = _normalize_action_type(output.get("action_type"))
        timing_band = _normalize_timing_band(output.get("timing_band"))
        labels = _normalize_labels(output.get("labels"), members)
        dashboard_visible = bool(output.get("dashboard_visible")) and bool(title and summary)
        group = upsert_mail_group(
            database_url,
            user_id=user_id,
            group_key=group_key,
            group_type=compact_text(str(output.get("group_type") or "other"))[:80] or "other",
            ai_title=title[:180],
            ai_summary=summary[:1200],
            labels=labels,
            action_needed=bool(output.get("action_needed")),
            action_type=action_type,
            priority=max(0, min(100, _int_or_default(output.get("priority"), 0))),
            timing_band=timing_band,
            dashboard_visible=dashboard_visible,
            latest_message_at=latest.internal_date or latest.updated_at,
            latest_message_id=latest.message_id,
            generated_from_hash=group_hash,
            generated_at=datetime.now(timezone.utc).isoformat(),
            enrichment_status="ready",
            membership_source="ai_batch",
            ai_model=settings.openai_model,
            ai_error=None,
            ai_generated_at=datetime.now(timezone.utc).isoformat(),
        )
        replace_group_members(
            database_url,
            user_id=user_id,
            group_id=group.id,
            members=[(message, "ai_batch", 0.95) for message in members],
        )
        created += 1
        if dashboard_visible:
            visible_created += 1
    return created, visible_created, used_ids


def _message_for_ai(message: GmailMessageRecord) -> dict[str, Any]:
    return {
        "message_id": message.message_id,
        "gmail_thread_id": message.gmail_thread_id,
        "subject": message.subject,
        "sender": message.sender,
        "recipients": message.recipients,
        "date": message.internal_date,
        "snippet": message.snippet,
        "text": clean_ai_text(message.text_body or message.snippet or "", max_chars=900),
        "signals": message.extracted_signals,
        "labels": message.label_ids,
    }


def _candidate_context_for_ai(messages: list[GmailMessageRecord]) -> dict[str, Any]:
    context: dict[str, Any] = {
        "gmail_thread_clusters": defaultdict(list),
        "subject_domain_clusters": defaultdict(list),
        "strict_signal_clusters": defaultdict(list),
    }
    for message in messages:
        if message.gmail_thread_id:
            context["gmail_thread_clusters"][message.gmail_thread_id].append(message.message_id)
        signals = message.extracted_signals
        domain = str(signals.get("sender_domain") or sender_domain(message.sender))
        subject = str(signals.get("normalized_subject") or message.subject or "")[:120]
        if subject:
            context["subject_domain_clusters"][f"{domain}:{subject}"].append(message.message_id)
        for signal_name in ["order_id", "ticket_id", "tracking_id", "invoice_id", "booking_id", "application_id"]:
            value = signals.get(signal_name)
            if isinstance(value, str) and value:
                context["strict_signal_clusters"][f"{signal_name}:{domain}:{value}"].append(message.message_id)
    return {
        key: {cluster_key: ids for cluster_key, ids in value.items() if len(ids) > 1}
        for key, value in context.items()
    }


def _ai_group_key(_client_group_key: Any, members: list[GmailMessageRecord], _index: int) -> str:
    member_hash = hashlib.sha256("\n".join(sorted(message.message_id for message in members)).encode("utf-8")).hexdigest()[:24]
    return f"ai:{member_hash}"


def _normalize_action_type(value: Any) -> str:
    action_type = compact_text(str(value or "none")).lower()
    return action_type if action_type in ALLOWED_ACTION_TYPES else "open"


def _normalize_timing_band(value: Any) -> str:
    timing_band = compact_text(str(value or "later")).lower()
    return timing_band if timing_band in ALLOWED_TIMING_BANDS else "later"


def _normalize_labels(value: Any, members: list[GmailMessageRecord]) -> list[str]:
    output = [str(label).strip()[:60] for label in value if str(label).strip()] if isinstance(value, list) else []
    gmail_labels = [label for message in members for label in message.label_ids]
    return sorted(set(output + gmail_labels))[:16]


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
    }


def _candidate_groups(messages: list[GmailMessageRecord]) -> dict[str, list[GmailMessageRecord]]:
    groups: dict[str, list[GmailMessageRecord]] = defaultdict(list)
    for message in messages:
        signals = message.extracted_signals
        domain = str(signals.get("sender_domain") or sender_domain(message.sender))
        for signal_name in ["order_id", "ticket_id", "tracking_id", "invoice_id", "booking_id", "application_id"]:
            value = signals.get(signal_name)
            if isinstance(value, str) and value:
                groups[f"{signal_name}:{domain}:{value}"].append(message)
                break
        else:
            lifecycle_key = _entity_lifecycle_group_key(message, domain=domain)
            if lifecycle_key:
                groups[lifecycle_key].append(message)
            elif message.gmail_thread_id:
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


def _entity_lifecycle_group_key(message: GmailMessageRecord, *, domain: str) -> str | None:
    subject = str(message.extracted_signals.get("normalized_subject") or message.subject or "")
    text = " ".join([subject, message.sender or ""])
    if not LIFECYCLE_WORD_RE.search(text):
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
    latest = max(messages, key=lambda item: item.internal_date or item.updated_at)
    text = " ".join([latest.subject or "", latest.snippet or "", latest.text_body or ""])
    lifecycle = bool(LIFECYCLE_WORD_RE.search(text))
    action = bool(ACTION_WORD_RE.search(text))
    group_type = _group_type_from_key(group_key, lifecycle=lifecycle)
    gmail_labels = sorted({label for message in messages for label in message.label_ids})
    labels = [group_type, *gmail_labels]
    if action:
        labels.append("needs_action")
    return {
        "group_type": group_type,
        "ai_title": _fallback_title(latest, group_type),
        "ai_summary": compact_text(latest.snippet or latest.text_body or latest.subject or "Gmail update")[:500],
        "labels": labels,
        "action_needed": action,
        "action_type": _action_type(text),
        "priority": 80 if action else 30 if lifecycle else 10,
        "timing_band": "now" if action and "UNREAD" in latest.label_ids else "today" if action else "later",
        "dashboard_visible": action or lifecycle,
    }


def _normalize_enrichment(parsed: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    timing = str(parsed.get("timing_band") or fallback["timing_band"])
    if timing not in ALLOWED_TIMING_BANDS:
        timing = fallback["timing_band"]
    action_type = _normalize_action_type(parsed.get("action_type") or fallback["action_type"])
    return {
        "group_type": str(parsed.get("group_type") or fallback["group_type"])[:80],
        "ai_title": compact_text(str(parsed.get("ai_title") or fallback["ai_title"]))[:180],
        "ai_summary": compact_text(str(parsed.get("ai_summary") or fallback["ai_summary"]))[:1200],
        "labels": [str(label)[:60] for label in parsed.get("labels", fallback["labels"]) if str(label).strip()][:10]
        if isinstance(parsed.get("labels", fallback["labels"]), list)
        else fallback["labels"],
        "action_needed": bool(parsed.get("action_needed", fallback["action_needed"])),
        "action_type": action_type,
        "priority": _normalize_priority(parsed.get("priority"), fallback["priority"]),
        "timing_band": timing,
        "dashboard_visible": bool(parsed.get("dashboard_visible", fallback["dashboard_visible"])),
    }


def _normalize_priority(value: Any, fallback: Any) -> int:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"urgent", "critical"}:
            return 95
        if normalized == "high":
            return 80
        if normalized == "medium":
            return 50
        if normalized == "low":
            return 20
    try:
        return max(0, min(100, int(value if value is not None else fallback)))
    except (TypeError, ValueError):
        try:
            return max(0, min(100, int(fallback)))
        except (TypeError, ValueError):
            return 0


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


def _group_type_from_key(group_key: str, *, lifecycle: bool) -> str:
    if group_key.startswith("order_id") or group_key.startswith("tracking_id"):
        return "order_lifecycle"
    if group_key.startswith("ticket_id"):
        return "support_case"
    if group_key.startswith("invoice_id"):
        return "billing"
    if group_key.startswith("booking_id"):
        return "travel"
    if group_key.startswith("application_id"):
        return "application"
    if lifecycle:
        return "lifecycle"
    return "conversation"


def _fallback_title(message: GmailMessageRecord, group_type: str) -> str:
    subject = compact_text(message.subject or "")
    if subject:
        return subject
    domain = sender_domain(message.sender)
    return f"{group_type.replace('_', ' ').title()} from {domain}"


def _action_type(text: str) -> str:
    lowered = text.lower()
    if "pay" in lowered or "payment" in lowered or "invoice" in lowered:
        return "pay"
    if "confirm" in lowered or "rsvp" in lowered:
        return "confirm"
    if "reply" in lowered or "respond" in lowered:
        return "reply"
    if "approve" in lowered:
        return "confirm"
    if "track" in lowered or "delivered" in lowered:
        return "track"
    return "open"


def _attention_item_from_group(group: MailGroupRecord) -> AttentionItem:
    action_type = "external" if group.action_needed else "none"
    return AttentionItem(
        id=f"mail-group:{group.id}",
        entity_id=group.id,
        user_id=group.user_id,
        need_type="decision" if group.action_needed else "awareness",
        action_type=action_type,
        effort_level="quick",
        timing_band=group.timing_band if group.timing_band in {"now", "today", "later", "hidden"} else "later",
        action_confidence="medium",
        primary_action=group.action_type or "open",
        fallback_action="open",
        title=group.ai_title,
        why_this_is_here=group.ai_summary,
        detail=AttentionItemDetail(
            body=[group.ai_summary],
            action_label="Open group",
            source_label="Gmail",
        ),
        importance_level="high" if group.priority >= 70 else "medium" if group.priority >= 35 else "low",
        lifecycle_state="active" if group.action_needed else "scheduled",
        current_state="open" if group.action_needed else "waiting",
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


def _gmail_row_from_group(group: MailGroupRecord, messages: list[GmailMessageRecord]) -> GmailThreadRow:
    latest_message = max(messages, key=lambda item: item.internal_date or item.updated_at) if messages else None
    participants = _participants(messages)
    title = group.ai_title
    summary = group.ai_summary
    action_type = group.action_type if group.action_type in {"pay", "reply", "confirm", "track", "review", "open", "none"} else "none"
    return GmailThreadRow(
        thread_id=group.id,
        entity_id=group.id,
        title=title,
        href=f"/gmail/threads/{group.id}",
        latest_source_record_id=group.latest_message_id or group.id,
        latest_received_at=group.latest_message_at or group.updated_at,
        latest_message_at=group.latest_message_at or group.updated_at,
        latest_subject=title,
        latest_sender=latest_message.sender if latest_message else None,
        sender=latest_message.sender if latest_message else None,
        participants=participants,
        message_count=max(1, len(messages)),
        summary=summary,
        snippet=summary,
        label_ids=group.labels,
        labels=group.labels,
        unread="unread" in {label.lower() for label in group.labels},
        action_needed=group.action_needed,
        action_type=action_type,  # type: ignore[arg-type]
        action_type_key=action_type,  # type: ignore[arg-type]
        priority=group.priority,
        dashboard_visible=group.dashboard_visible,
        current_state="open" if group.action_needed else "waiting",
        lifecycle_state="active" if group.action_needed else "scheduled",
        lifecycle_updates=[
            {
                "source_record_id": message.message_id,
                "received_at": message.internal_date or message.updated_at,
                "subject": message.subject or group.ai_title,
                "sender": message.sender,
                "summary": message.snippet,
            }
            for message in messages[-3:]
        ],
        enrichment_status=group.enrichment_status,
    )


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


def _thread_message_from_gmail(message: GmailMessageRecord) -> ThreadMessage:
    html_body = html_body_for_reader(message.html_body_sanitized)
    html_render_document = html_render_document_for_reader(message.html_render_document)
    return ThreadMessage(
        id=message.message_id,
        source="gmail",
        thread_id=message.gmail_thread_id,
        from_address=message.sender,
        to=message.recipients.get("to") if isinstance(message.recipients.get("to"), str) else None,
        cc=message.recipients.get("cc") if isinstance(message.recipients.get("cc"), str) else None,
        bcc=message.recipients.get("bcc") if isinstance(message.recipients.get("bcc"), str) else None,
        subject=message.subject,
        body=message.text_body or message.snippet or "",
        html_body=html_body,
        html_render_document=html_render_document,
        reader=build_thread_message_reader(
            html_render_document=html_render_document,
            html_body=html_body,
            text_body=message.text_body,
            snippet=message.snippet,
            headers=message.headers,
        ),
        snippet=message.snippet,
        label_ids=message.label_ids,
        received_at=message.internal_date or message.updated_at,
    )


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
    return not has_persisted_renderable_body(
        text_body=message.text_body,
        html_body=message.html_body_sanitized,
        html_render_document=message.html_render_document,
        raw_payload=message.raw_payload,
    )


def _bucket_rows(rows: list[GmailThreadRow]) -> list[GmailThreadSection]:
    reference = datetime.now().astimezone().date()
    buckets: OrderedDict[str, tuple[str, list[GmailThreadRow]]] = OrderedDict()
    for row in rows:
        date = _parse_date(row.latest_received_at).date()
        age = (reference - date).days
        if age <= 0:
            key, title = "today", "Today"
        elif age == 1:
            key, title = "yesterday", "Yesterday"
        elif age <= 7:
            key, title = "last-seven-days", "Last seven days"
        else:
            key, title = f"month-{date.year}-{date.month}", date.strftime("%B %Y")
        if key not in buckets:
            buckets[key] = (title, [])
        buckets[key][1].append(row)
    return [GmailThreadSection(id=key, title=title, rows=items) for key, (title, items) in buckets.items()]


def _parse_date(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now().astimezone()
    return parsed.astimezone() if parsed.tzinfo else parsed.astimezone()


def _mailbox_label(label: str) -> MailboxLabel:
    normalized = label.strip().lower()
    return normalized if normalized in {"inbox", "sent", "drafts", "trash", "archive", "all"} else "inbox"  # type: ignore[return-value]


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
    if label == "archive":
        return "INBOX" not in labels and "TRASH" not in labels
    return True
