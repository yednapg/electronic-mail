from __future__ import annotations

"""Mail group product pipeline: grouping, enrichment, dashboard mapping."""

from collections import defaultdict, OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses, parseaddr
import hashlib
import html
import json
import logging
import re
from typing import Any
from urllib.parse import quote

from app.core.config import Settings
from app.db.jobs import count_active_jobs, enqueue_job, get_queue_health
from app.db.mail_groups import (
    EntityOutcomeRecord,
    GmailMessageRecord,
    MailGroupRecord,
    MailObjectBundle,
    ManualTaskRecord,
    VisibleMailGroupRecord,
    count_mailbox_threads,
    count_mail_groups,
    count_mail_groups_by_enrichment_status,
    count_mail_groups_by_enrichment_status_since,
    count_dashboard_mail_groups,
    count_pending_thread_actions,
    count_ready_mail_groups_since,
    get_app_session_snapshot,
    get_import_state,
    get_latest_entity_outcomes,
    latest_mail_group_ai_error,
    latest_gmail_mailbox_revision,
    get_mail_group_by_key,
    get_mail_group_detail,
    get_smart_inbox_row,
    list_dashboard_mail_groups,
    list_group_messages,
    list_mailbox_thread_page,
    list_mail_groups_for_gmail_threads,
    list_mail_groups,
    list_mail_object_bundles,
    list_messages_by_ids,
    list_messages_for_gmail_thread,
    list_messages_for_visible_groups,
    list_messages_for_groups,
    list_open_manual_tasks,
    list_pending_mail_groups,
    list_recent_messages,
    list_recent_messages_since,
    mark_import_completed,
    mark_import_error,
    oldest_imported_message_at,
    replace_group_members,
    replace_smart_inbox_projection,
    replace_visible_mail_projection,
    update_gmail_message_ai_titles,
    update_mail_group_ai_summary,
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
    SmartInboxResponse,
    SmartInboxRow,
    SmartInboxSection,
    SmartRelatedSuggestion,
    SmartReadinessResponse,
    SmartWorkItem,
    SmartWorkQueueResponse,
    ThreadAttachment,
    ThreadMessage,
    ThreadReaderResponse,
)
from app.services.email_extraction import (
    BOOKING_RE,
    build_thread_message_reader,
    clean_ai_text,
    compact_text,
    has_persisted_renderable_body,
    html_body_for_reader,
    html_render_document_for_reader,
    ORDER_RE,
    parse_gmail_message,
    sender_domain,
    TRACKING_RE,
)
from app.services.grouping_projection import (
    INBOX_STRICT_WORKFLOWS,
    _crosses_unrelated_entities,
    _has_conflicting_extracted_references,
    build_inbox_projection,
    canonical_entity_for_messages,
)
from app.services.attention_classifier import (
    ACTION_TYPES as ALLOWED_ACTION_TYPES,
    TIMING_BANDS as ALLOWED_TIMING_BANDS,
    apply_attention_policy,
    attention_enrichment_payload,
    deterministic_classification,
    facts_to_json,
    is_status_noise_classification,
    is_terminal_classification,
    polish_inbox_title,
    workflow_cluster_key,
)
from app.services.integrations.google import GMAIL_SEND_SCOPE, check_user_google_credentials, missing_google_scopes
from app.services.mail_group_config import (
    AI_LIFECYCLE_MIN_INBOX_CONFIDENCE,
    AI_LIFECYCLE_PROJECTION_DAYS,
    AI_LIFECYCLE_PROJECTION_MAX_GROUPS,
    AI_LIFECYCLE_PROJECTION_MAX_OUTPUT_TOKENS,
    AI_LIFECYCLE_PROJECTION_MESSAGE_LIMIT,
    APP_SESSION_MAILBOX_LIMIT,
    APP_SESSION_PROJECTION_VERSION,
    BACKFILL_BATCH_SIZE,
    BODY_WARMUP_GROUP_LIMIT,
    DASHBOARD_DAYS,
    DOMAIN_LABEL_WORDS,
    DOMAIN_OWNER_SUFFIX_WORDS,
    DOMAIN_PLATFORM_LABELS,
    DOMAIN_SERVICE_LABELS,
    DOMAIN_TITLE_SUFFIX_WORDS,
    DOMAIN_WORD_TITLES,
    FIRST_BATCH_SIZE,
    FIRST_RUN_HOT_WINDOW_BATCH_SIZE,
    FIRST_RUN_AI_CANDIDATE_LIMIT,
    FIRST_RUN_AI_MAX_GROUPS,
    FIRST_RUN_AI_MAX_OUTPUT_TOKENS,
    GENERIC_SENDER_SLUGS,
    LOCALIZATION_PLACEHOLDER_RE,
    MAILBOX_BUNDLE_FALLBACK_KEYWORD_STOP_WORDS,
    MAILBOX_BUNDLE_TITLE_MAX_CHARS,
    MAILBOX_BUNDLE_TOPIC_STOP_WORDS,
    MAILBOX_DISPLAY_CLUSTER_PREFIX,
    MAILBOX_DISPLAY_CLUSTER_WORKFLOW_TYPES,
    MAILBOX_EXACT_ABSORPTION_SIGNALS,
    MAILBOX_PROVIDER_STREAM_MAX_SPAN_DAYS,
    MAILBOX_REBUILD_LIMIT,
    MAILBOX_REFERENCE_FAMILY_BY_SIGNAL,
    MAILBOX_TASK_REFERENCE_SIGNALS,
    MAILBOX_TOPIC_TITLE_GENERIC_WORDS,
    MAIL_GROUP_ENRICH_BATCH_SIZE,
    MAIL_GROUP_ENRICH_MAX_OUTPUT_TOKENS,
    RECENT_VISIBLE_DAYS,
    SERVICE_CHANNEL_WORDS,
    SMART_BOOKING_REFERENCE_RE,
    SMART_FIRST_READY_TARGET_MESSAGES,
    SMART_HOT_WINDOW_DAYS,
    SMART_HOT_WINDOW_MESSAGE_CAP,
    SMART_HOT_WINDOW_THREAD_CAP,
    SMART_OFFLINE_WARMUP_THREAD_LIMIT,
    SMART_REPAIR_REFERENCE_RE,
    SMART_SUPPORT_REFERENCE_RE,
    SMART_TRADE_REFERENCE_RE,
    SMART_WORKFLOW_ACCOUNT_TOKENS,
    SMART_WORKFLOW_APPLICATION_TOKENS,
    SMART_WORKFLOW_LIMIT_TOKENS,
    SMART_WORKFLOW_MAX_SPAN_DAYS,
    SMART_WORKFLOW_PLATFORM_STOP_WORDS,
    SMART_WORKFLOW_REMITTANCE_TOKENS,
    SMART_WORKFLOW_TOKEN_STOP_WORDS,
    TITLE_PHRASE_STOP_STARTS,
    TOPIC_STOP_WORDS,
)
from app.services.mailbox_events import GMAIL_PUBSUB_RECEIVED, latest_event
from app.services.smart_inbox_quality import audit_smart_inbox_quality, smart_inbox_response_is_product_ready

logger = logging.getLogger(__name__)

SMART_ROW_READER_PREFIX = "smart-row:"


@dataclass(frozen=True)
class MailboxDisplayClusterEntry:
    thread_id: str
    messages: list[GmailMessageRecord]
    row: GmailThreadRow
    cluster_key: str | None


def _google_auth_state(settings: Settings, *, user_id: str) -> GoogleAuthState:
    connected = user_can_write_gmail(str(settings.database_path), user_id=user_id)
    missing_scopes = missing_google_scopes(settings, user_id=user_id, required_scopes=[GMAIL_SEND_SCOPE]) if connected else []
    return GoogleAuthState(
        available=settings.google_configured,
        connected=connected,
        connect_url=None if connected else f"{settings.backend_origin}/auth/google",
        can_send_mail=connected and not missing_scopes,
        missing_scopes=missing_scopes,
    )


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
    hot_window_completed = bool(
        state
        and (
            getattr(state, "hot_window_completed_at", None)
            or getattr(state, "full_backfill_completed_at", None)
        )
    )
    if state is not None and state.first_batch_imported_at and not state.first_groups_ready_at and hot_window_completed:
        enqueue_job(
            database_url,
            kind="first_run_ai_grouping",
            queue="critical",
            user_id=user_id,
            dedupe_key=f"first-run-ai-grouping-hot-window:{user_id}",
            priority=98,
            payload={"user_id": user_id, "batch_size": FIRST_BATCH_SIZE, "source": "hot_window_recovery"},
        )
    active_backfills = count_active_jobs(database_url, user_id=user_id, kinds=["gmail_backfill"])
    full_backfill_completed_at = getattr(state, "full_backfill_completed_at", None) if state is not None else None
    full_backfill_needed = bool(state and state.first_batch_imported_at and not full_backfill_completed_at)
    if full_backfill_needed and not active_backfills:
        hot_window_pending = not hot_window_completed
        enqueue_job(
            database_url,
            kind="gmail_backfill",
            queue="critical" if hot_window_pending else "slow",
            user_id=user_id,
            dedupe_key=f"gmail-backfill:{user_id}:resume",
            priority=90 if hot_window_pending else 80,
            payload={
                "user_id": user_id,
                "batch_size": FIRST_RUN_HOT_WINDOW_BATCH_SIZE if hot_window_pending else BACKFILL_BATCH_SIZE,
            },
        )
    status_counts = count_mail_groups_by_enrichment_status(database_url, user_id=user_id)
    if status_counts.get("pending", 0) > 0:
        if state is not None and state.first_batch_imported_at and hot_window_completed:
            _enqueue_hot_window_mail_group_enrich(settings, user_id=user_id)
        else:
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
    dashboard = DashboardResponse.model_validate(snapshot.dashboard)
    mailbox = MailboxResponse.model_validate(snapshot.mailbox)
    sync = AppSessionSyncState.model_validate(snapshot.sync)
    smart_inbox = _smart_inbox_from_snapshot(snapshot.smart_inbox, mailbox=mailbox)
    status_counts = count_mail_groups_by_enrichment_status(database_url, user_id=user.id)
    recent_since = (datetime.now(timezone.utc) - timedelta(days=_recent_visible_days(settings))).isoformat()
    recent_status_counts = count_mail_groups_by_enrichment_status_since(
        database_url,
        user_id=user.id,
        since_iso=recent_since,
        mailbox_label="inbox",
    )
    last_ai_error = latest_mail_group_ai_error(database_url, user_id=user.id)
    live_runtime = _mail_runtime_status(database_url, status_counts=status_counts, last_ai_error=last_ai_error)
    dashboard.runtime_status = {**dashboard.runtime_status, **live_runtime}
    sync = sync.model_copy(
        update={
            "enrichment_pending_count": status_counts.get("pending", 0),
            "ready_group_count": status_counts.get("ready", 0),
            "last_ai_error": last_ai_error,
            "last_error": sync.last_error or (last_ai_error if status_counts.get("ready", 0) == 0 else None),
        }
    )
    readiness = _readiness_from_snapshot(
        settings,
        user=user,
        dashboard=dashboard,
        mailbox=mailbox,
        sync=sync,
        recent_since=recent_since,
        recent_status_counts=recent_status_counts,
        smart_inbox=smart_inbox,
    )
    smart_work_queue = _smart_work_queue_from_snapshot(snapshot.smart_work_queue, dashboard=dashboard, smart_inbox=smart_inbox)
    smart_readiness = _smart_readiness_from_snapshot(
        snapshot.smart_readiness,
        mailbox=mailbox,
        sync=sync,
        smart_inbox=smart_inbox,
    )
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
        smart_inbox=smart_inbox,
        smart_work_queue=smart_work_queue,
        smart_readiness=smart_readiness,
    )


def _empty_app_session_response(settings: Settings, *, user, auth: GoogleAuthState) -> AppSessionResponse:
    dashboard = DashboardResponse(auth=auth, profile=user.profile, feed=FeedResponse())
    mailbox = MailboxResponse(label="inbox", total_threads=0)
    smart_inbox = _smart_inbox_from_mailbox(mailbox)
    sync = AppSessionSyncState(
        last_sync_at=None,
        last_error=None,
        enrichment_pending_count=0,
        ready_group_count=0,
        oldest_imported_at=None,
        full_import_running=False,
        full_import_completed=False,
    )
    readiness = _readiness_from_snapshot(
        settings,
        user=user,
        dashboard=dashboard,
        mailbox=mailbox,
        sync=sync,
        recent_status_counts={"ready": 0, "pending": 0, "failed": 0},
        smart_inbox=smart_inbox,
    )
    smart_work_queue = _smart_work_queue_from_smart_inbox(smart_inbox)
    smart_readiness = _smart_readiness_from_mailbox(mailbox=mailbox, sync=sync, smart_inbox=smart_inbox)
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
        smart_inbox=smart_inbox,
        smart_work_queue=smart_work_queue,
        smart_readiness=smart_readiness,
    )


def _readiness_from_snapshot(
    settings: Settings,
    *,
    user,
    dashboard: DashboardResponse,
    mailbox: MailboxResponse,
    sync: AppSessionSyncState,
    recent_since: str | None = None,
    recent_status_counts: dict[str, int] | None = None,
    smart_inbox: SmartInboxResponse | None = None,
) -> PostLoginReadinessResponse:
    state = get_import_state(str(settings.database_path), user_id=user.id)
    ready_dashboard_count = len(dashboard.feed.now) + len(dashboard.feed.today) + len(dashboard.feed.worth_knowing)
    hot_window_ready = _smart_inbox_product_ready(state=state, smart_inbox=smart_inbox)
    if not hot_window_ready and not _smart_inbox_has_rows(smart_inbox):
        hot_window_ready = hot_window_product_ready(
            settings,
            user_id=user.id,
            state=state,
            recent_since=recent_since,
            status_counts=recent_status_counts,
            mailbox=mailbox,
        )
    recent_ready_count = recent_status_counts.get("ready", 0) if recent_status_counts is not None else 0
    has_prior_product = bool(((state and state.first_groups_ready_at) or recent_ready_count) and hot_window_ready)
    mode = "returning" if has_prior_product else "first_time"
    mailbox_ready = mailbox.total_threads > 0 or recent_ready_count > 0
    dashboard_ready = bool(ready_dashboard_count > 0 and ((state and state.first_dashboard_ready_at) or sync.ready_group_count > 0))
    if mode == "returning":
        ready_to_enter = mailbox_ready
    else:
        ready_to_enter = bool(
            state
            and state.first_batch_imported_at
            and state.first_groups_ready_at
            and hot_window_ready
            and mailbox_ready
        )
    stage = _post_login_stage(
        mode=mode,
        ready_to_enter=ready_to_enter,
        state=state,
        active_setup_jobs=0,
        mailbox_ready=mailbox_ready,
        dashboard_ready=dashboard_ready,
        hot_window_ready=hot_window_ready,
    )
    display_name = _first_name(user.profile) or user.profile.display_name or user.email
    return PostLoginReadinessResponse(
        mode=mode,  # type: ignore[arg-type]
        stage=stage,  # type: ignore[arg-type]
        ready_to_enter=ready_to_enter,
        dashboard_ready=dashboard_ready,
        mailbox_ready=mailbox_ready,
        ready_dashboard_count=ready_dashboard_count,
        ready_mail_group_count=recent_ready_count,
        full_import_running=sync.full_import_running,
        full_import_completed=sync.full_import_completed,
        user_display_name=display_name,
        error_message=sync.last_error if sync.last_error and not ready_to_enter else None,
    )


def _smart_inbox_from_snapshot(payload: dict[str, Any], *, mailbox: MailboxResponse) -> SmartInboxResponse:
    snapshot = SmartInboxResponse.model_validate(payload or {})
    if snapshot.total_rows > 0 or any(section.rows for section in snapshot.sections):
        return snapshot
    return _smart_inbox_from_mailbox(mailbox)


def _smart_inbox_from_mailbox(mailbox: MailboxResponse) -> SmartInboxResponse:
    all_rows: list[SmartInboxRow] = []
    for mailbox_section in mailbox.sections:
        rows = [_smart_row_from_mailbox_row(row, section_id=mailbox_section.id) for row in mailbox_section.rows]
        all_rows.extend(rows)
    all_rows = _smart_product_visible_rows(all_rows)
    all_rows = _smart_rows_absorbing_matching_references(all_rows)
    return _smart_inbox_response_from_rows(_smart_hot_window_rows(all_rows), generated_at=mailbox.generated_at)


def _smart_inbox_from_mailbox_and_objects(
    database_url: str,
    *,
    user_id: str,
    mailbox: MailboxResponse,
    object_limit: int = 100,
) -> SmartInboxResponse:
    base = _smart_inbox_from_mailbox(mailbox)
    bundles = list_mail_object_bundles(database_url, user_id=user_id, limit=object_limit)
    object_rows = [_smart_row_from_mail_object_bundle(bundle) for bundle in bundles if len(bundle.messages) > 1]
    if not object_rows:
        return base
    object_rows, absorbed_base_row_ids = _smart_object_rows_absorbing_overlapping_groups(
        object_rows,
        [row for section in base.sections for row in section.rows],
    )
    covered_message_ids = {message_id for row in object_rows for message_id in row.source_message_ids}
    rows: list[SmartInboxRow] = []
    rows.extend(object_rows)
    for section in base.sections:
        for row in section.rows:
            if row.id in absorbed_base_row_ids:
                continue
            row_message_ids = set(row.source_message_ids)
            if row_message_ids and row_message_ids <= covered_message_ids:
                continue
            rows.append(row)
    rows = _smart_product_visible_rows(rows)
    rows = _smart_rows_absorbing_matching_references(rows)
    rows.sort(key=lambda row: row.latest_message_at or "", reverse=True)
    return _smart_inbox_response_from_rows(_smart_hot_window_rows(rows), generated_at=mailbox.generated_at)


def _smart_product_visible_rows(rows: list[SmartInboxRow]) -> list[SmartInboxRow]:
    return [row for row in rows if _smart_row_is_product_visible(row)]


def _smart_row_is_product_visible(row: SmartInboxRow) -> bool:
    reason = row.grouping_reason if isinstance(row.grouping_reason, dict) else {}
    metadata = reason.get("metadata") if isinstance(reason.get("metadata"), dict) else {}
    workflow = metadata.get("workflow") if isinstance(metadata.get("workflow"), dict) else {}
    family = compact_text(str(workflow.get("family") or metadata.get("family") or metadata.get("group_type") or "")).lower()
    requires_action = bool(workflow.get("requires_user_action") or metadata.get("action_needed"))
    if family in {"marketing", "newsletter"} and not requires_action:
        return False
    return True


def _smart_inbox_with_offline_status(
    database_url: str,
    *,
    user_id: str,
    smart_inbox: SmartInboxResponse,
) -> SmartInboxResponse:
    message_ids = _dedupe_strings(
        [message_id for section in smart_inbox.sections for row in section.rows for message_id in row.source_message_ids]
    )
    messages = list_messages_by_ids(database_url, user_id=user_id, message_ids=message_ids)
    messages_by_id = {message.message_id: message for message in messages}
    sections: list[SmartInboxSection] = []
    for section in smart_inbox.sections:
        rows = [
            row.model_copy(update={"offline_status": _smart_row_offline_status(row, messages_by_id)})
            for row in section.rows
        ]
        sections.append(section.model_copy(update={"rows": rows}))
    return smart_inbox.model_copy(update={"sections": sections})


def _smart_row_offline_status(row: SmartInboxRow, messages_by_id: dict[str, GmailMessageRecord]) -> str:
    if not row.source_message_ids:
        return "partial"
    messages: list[GmailMessageRecord] = []
    for message_id in row.source_message_ids:
        message = messages_by_id.get(message_id)
        if message is None:
            return "partial"
        messages.append(message)
    if all(not _needs_body_fetch(message) for message in messages):
        return "ready"
    if any((message.body_fetch_status or "") == "failed" and _needs_body_fetch(message) for message in messages):
        return "failed"
    return "partial"


def _smart_row_from_mail_object_bundle(bundle: MailObjectBundle) -> SmartInboxRow:
    messages = sorted(bundle.messages, key=lambda item: item.internal_date or item.updated_at or "", reverse=True)
    latest = messages[0]
    row_id = f"smart-object:{bundle.object.id}"
    source_message_ids = _dedupe_strings([message.message_id for message in messages])
    source_thread_ids = _dedupe_strings([message.gmail_thread_id for message in messages if message.gmail_thread_id])
    return SmartInboxRow(
        id=row_id,
        row_key=f"mail-object:{bundle.object.canonical_key}",
        row_type="verified_group",
        title=bundle.object.title,
        summary=bundle.object.summary or _object_bundle_summary(bundle),
        primary_sender=_smart_object_primary_sender_for_bundle(bundle, messages),
        latest_message_at=latest.internal_date or latest.updated_at,
        latest_message_id=latest.message_id,
        reader_thread_id=f"{SMART_ROW_READER_PREFIX}{row_id}",
        source_thread_ids=source_thread_ids,
        source_message_ids=source_message_ids,
        confidence_tier="exact",
        confidence=max(0.0, min(1.0, bundle.object.confidence or 1.0)),
        grouping_reason={
            "source": "mail_object",
            "object_id": bundle.object.id,
            "object_type": bundle.object.object_type,
            "canonical_key": bundle.object.canonical_key,
            "member_count": len(messages),
            "evidence": bundle.object.evidence,
        },
        offline_status="partial",
        readiness="ready",
        action_type="open",
        priority=70,
    )


def _smart_object_primary_sender_for_bundle(bundle: MailObjectBundle, messages: list[GmailMessageRecord]) -> str | None:
    if bundle.object.canonical_key.startswith("trade_id:"):
        return "FX Retail"
    return _smart_object_primary_sender(messages)


def _smart_object_primary_sender(messages: list[GmailMessageRecord]) -> str | None:
    for message in messages:
        if not _sender_display_name_is_generic(message.sender):
            return _mailbox_display_sender(message, "inbox") or message.sender
    if not messages:
        return None
    return _mailbox_display_sender(messages[0], "inbox") or messages[0].sender


def _sender_display_name_is_generic(sender: str | None) -> bool:
    name, address = parseaddr(sender or "")
    normalized_name = compact_text(name).lower()
    if normalized_name in {"default user", "unknown sender"}:
        return True
    if not normalized_name and not address:
        return True
    return False


def _object_bundle_summary(bundle: MailObjectBundle) -> str:
    message_count = len(bundle.messages)
    object_name = bundle.object.title or bundle.object.canonical_key
    return f"{message_count} emails related to {object_name}."


def _smart_object_rows_absorbing_overlapping_groups(
    object_rows: list[SmartInboxRow],
    base_rows: list[SmartInboxRow],
) -> tuple[list[SmartInboxRow], set[str]]:
    absorbed_base_row_ids: set[str] = set()
    merged_rows = list(object_rows)

    for base_row in base_rows:
        if not _smart_base_row_can_be_absorbed_by_object(base_row):
            continue
        overlapping_indexes = [
            index
            for index, object_row in enumerate(merged_rows)
            if _smart_rows_overlap(base_row, object_row)
        ]
        if len(overlapping_indexes) != 1:
            continue
        index = overlapping_indexes[0]
        merged_rows[index] = _smart_object_row_absorbing_base_row(merged_rows[index], base_row)
        absorbed_base_row_ids.add(base_row.id)

    return merged_rows, absorbed_base_row_ids


def _smart_base_row_can_be_absorbed_by_object(row: SmartInboxRow) -> bool:
    if row.grouping_reason.get("source") != "mailbox_projection":
        return False
    if row.row_type not in {"verified_group", "related_bundle"}:
        return False
    if _smart_row_message_count(row) < 2:
        return False
    return bool(row.source_message_ids or row.source_thread_ids)


def _smart_rows_overlap(left: SmartInboxRow, right: SmartInboxRow) -> bool:
    left_message_ids = set(left.source_message_ids)
    right_message_ids = set(right.source_message_ids)
    if left_message_ids and right_message_ids and left_message_ids & right_message_ids:
        return True
    left_thread_ids = set(left.source_thread_ids)
    right_thread_ids = set(right.source_thread_ids)
    return bool(left_thread_ids and right_thread_ids and left_thread_ids & right_thread_ids)


def _smart_rows_absorbing_matching_references(rows: list[SmartInboxRow]) -> list[SmartInboxRow]:
    if len(rows) < 2:
        return rows

    buckets: dict[tuple[str, str], list[SmartInboxRow]] = OrderedDict()
    for row in rows:
        if not _smart_reference_row_can_participate(row):
            continue
        for key in sorted(_smart_row_reference_keys(row)):
            buckets.setdefault(key, []).append(row)

    if not buckets:
        return rows

    covered_row_ids: set[str] = set()
    merged_rows: list[SmartInboxRow] = []
    for reference_key, bucket_rows in buckets.items():
        active_rows = [row for row in bucket_rows if row.id not in covered_row_ids]
        if len(active_rows) < 2:
            continue
        if not _smart_reference_bucket_is_safe(reference_key, active_rows):
            continue
        merged_rows.append(_smart_row_absorbing_reference_bucket(reference_key, active_rows))
        covered_row_ids.update(row.id for row in active_rows)

    if not merged_rows:
        return rows
    return [*merged_rows, *[row for row in rows if row.id not in covered_row_ids]]


def _smart_reference_row_can_participate(row: SmartInboxRow) -> bool:
    if row.grouping_reason.get("source") != "mailbox_projection":
        return False
    if row.row_type not in {"verified_group", "summarized_thread", "waiting", "normal"}:
        return False
    return bool(row.source_message_ids or row.source_thread_ids)


def _smart_row_reference_keys(row: SmartInboxRow) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for signal_name in ["ticket_id", "trade_id"]:
        keys.update((signal_name, value) for value in _smart_row_reference_values(row, signal_name=signal_name))
    return keys


def _smart_reference_bucket_is_safe(reference_key: tuple[str, str], rows: list[SmartInboxRow]) -> bool:
    signal_name, reference_value = reference_key
    if len(rows) < 2:
        return False
    if not _smart_reference_bucket_provider_matches(rows):
        return False
    for row in rows:
        values = _smart_row_reference_values(row, signal_name=signal_name)
        if reference_value not in values:
            return False
        if values - {reference_value}:
            return False
    return True


def _smart_reference_bucket_provider_matches(rows: list[SmartInboxRow]) -> bool:
    token_sets = [_smart_row_provider_tokens(row) for row in rows]
    if any(not tokens for tokens in token_sets):
        return False
    shared_tokens = set(token_sets[0])
    for tokens in token_sets[1:]:
        shared_tokens &= tokens
    return bool(shared_tokens)


def _smart_row_provider_tokens(row: SmartInboxRow) -> set[str]:
    metadata = row.grouping_reason.get("metadata") if isinstance(row.grouping_reason, dict) else {}
    reference = metadata.get("reference") if isinstance(metadata, dict) and isinstance(metadata.get("reference"), dict) else {}
    texts: list[str] = []
    provider = reference.get("provider") if isinstance(reference, dict) else None
    if isinstance(provider, str):
        texts.append(provider)
    for value in [row.primary_sender, row.title]:
        if value:
            texts.append(value)
        name, address = parseaddr(value or "")
        if name:
            texts.append(name)
        if address and (domain_owner := _domain_owner_title_from_address(address)):
            texts.append(domain_owner)

    stop_words = (
        SMART_WORKFLOW_TOKEN_STOP_WORDS
        | SERVICE_CHANNEL_WORDS
        | GENERIC_SENDER_SLUGS
        | {"bank", "co", "com", "email", "in", "india", "limited", "ltd", "mail", "net", "org", "pvt", "team", "via"}
    )
    return {
        token
        for text in texts
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 1 and token not in stop_words
    }


def _smart_row_absorbing_reference_bucket(
    reference_key: tuple[str, str],
    rows: list[SmartInboxRow],
) -> SmartInboxRow:
    signal_name, reference_value = reference_key
    ordered_rows = sorted(rows, key=lambda row: row.latest_message_at or "", reverse=True)
    primary = _smart_reference_primary_row(rows)
    latest = ordered_rows[0]
    source_message_ids = _dedupe_strings([message_id for row in ordered_rows for message_id in row.source_message_ids])
    source_thread_ids = _dedupe_strings([thread_id for row in ordered_rows for thread_id in row.source_thread_ids])
    grouping_reason = dict(primary.grouping_reason)
    absorbed = list(grouping_reason.get("absorbed_reference_rows") or [])
    absorbed.extend(
        {
            "row_key": row.row_key,
            "row_type": row.row_type,
            "source": row.grouping_reason.get("source"),
            "ai_group_id": row.grouping_reason.get("ai_group_id"),
        }
        for row in ordered_rows
        if row.id != primary.id
    )
    grouping_reason["absorbed_reference_rows"] = absorbed
    grouping_reason["reference_absorption"] = {
        "signal_name": signal_name,
        "value": reference_value,
        "source": "visible_exact_reference",
        "member_count": len(ordered_rows),
    }
    grouping_reason["message_count"] = len(source_message_ids)

    return primary.model_copy(
        update={
            "row_type": "verified_group",
            "summary": _smart_reference_summary(primary, ordered_rows),
            "latest_message_at": latest.latest_message_at,
            "latest_message_id": latest.latest_message_id,
            "source_thread_ids": source_thread_ids,
            "source_message_ids": source_message_ids,
            "confidence_tier": "exact",
            "confidence": max(0.96, *(row.confidence for row in ordered_rows)),
            "grouping_reason": grouping_reason,
            "readiness": _smart_reference_readiness(ordered_rows),
            "action_type": _smart_reference_action_type(primary, ordered_rows),
            "priority": max((row.priority for row in ordered_rows), default=primary.priority),
        }
    )


def _smart_reference_primary_row(rows: list[SmartInboxRow]) -> SmartInboxRow:
    row_type_rank = {
        "verified_group": 4,
        "related_bundle": 3,
        "summarized_thread": 2,
        "waiting": 1,
        "normal": 0,
        "update": 0,
    }
    return max(
        rows,
        key=lambda row: (
            _smart_row_message_count(row),
            row_type_rank.get(row.row_type, 0),
            row.priority,
            row.latest_message_at or "",
        ),
    )


def _smart_reference_summary(primary: SmartInboxRow, rows: list[SmartInboxRow]) -> str:
    if primary.summary:
        return primary.summary
    for row in rows:
        if row.summary:
            return row.summary
    return ""


def _smart_reference_readiness(rows: list[SmartInboxRow]) -> str:
    if any(row.readiness == "failed" for row in rows):
        return "failed"
    if all(row.readiness == "ready" for row in rows):
        return "ready"
    return "partial"


def _smart_reference_action_type(primary: SmartInboxRow, rows: list[SmartInboxRow]) -> str:
    if primary.action_type != "none":
        return primary.action_type
    for row in sorted(rows, key=lambda item: item.priority, reverse=True):
        if row.action_type != "none":
            return row.action_type
    return "open"


def _smart_row_reference_values(row: SmartInboxRow, *, signal_name: str) -> set[str]:
    values: set[str] = set()
    metadata = row.grouping_reason.get("metadata") if isinstance(row.grouping_reason, dict) else {}
    reference = metadata.get("reference") if isinstance(metadata, dict) and isinstance(metadata.get("reference"), dict) else {}
    if isinstance(reference, dict) and reference.get("signal_name") == signal_name:
        value = reference.get("value")
        if isinstance(value, str) and value:
            values.add(_mailbox_normalized_visible_reference(value))
    for item in row.grouping_reason.get("visible_references") or []:
        if not isinstance(item, dict) or item.get("signal_name") != signal_name:
            continue
        value = item.get("value")
        if isinstance(value, str) and value:
            values.add(_mailbox_normalized_visible_reference(value))
    if signal_name == "ticket_id":
        values.update(_smart_support_reference_values_from_text(" ".join([row.title, row.summary])))
    if signal_name == "trade_id":
        values.update(_smart_trade_reference_values_from_text(" ".join([row.title, row.summary])))
    return {value for value in values if value}


def _smart_mailbox_row_visible_references(row: GmailThreadRow) -> list[dict[str, str]]:
    text = _smart_mailbox_row_visible_reference_text(row)
    references = [
        {"signal_name": "ticket_id", "value": value}
        for value in sorted(_smart_support_reference_values_from_text(text))
    ]
    references.extend(
        {"signal_name": "trade_id", "value": value}
        for value in sorted(_smart_trade_reference_values_from_text(text))
    )
    for signal_name, pattern in [
        ("tracking_id", TRACKING_RE),
        ("order_id", ORDER_RE),
    ]:
        references.extend(
            {"signal_name": signal_name, "value": value}
            for value in sorted(_smart_contextual_reference_values_from_text(text, pattern=pattern))
        )
    booking_values = (
        _smart_contextual_reference_values_from_text(text, pattern=BOOKING_RE)
        | _smart_contextual_reference_values_from_text(text, pattern=SMART_BOOKING_REFERENCE_RE)
    )
    references.extend(
        {"signal_name": "booking_id", "value": value}
        for value in sorted(booking_values)
    )
    references.extend(
        {"signal_name": "repair_id", "value": value}
        for value in sorted(_smart_contextual_reference_values_from_text(text, pattern=SMART_REPAIR_REFERENCE_RE))
    )
    return references


def _smart_mailbox_row_visible_reference_text(row: GmailThreadRow) -> str:
    values: list[str | None] = [
        row.title,
        row.ai_title,
        row.summary,
        row.ai_summary,
        row.latest_subject,
        row.snippet,
    ]
    for child in row.children:
        values.extend([child.subject, child.ai_title, child.snippet])
    return " ".join(compact_text(value or "") for value in values if compact_text(value or ""))


def _smart_support_reference_values_from_text(text: str) -> set[str]:
    compacted = compact_text(text)
    return {
        _mailbox_normalized_visible_reference(match.group("value"))
        for match in SMART_SUPPORT_REFERENCE_RE.finditer(compacted)
        if _mailbox_normalized_visible_reference(match.group("value"))
    }


def _smart_trade_reference_values_from_text(text: str) -> set[str]:
    compacted = compact_text(text)
    return {
        _mailbox_normalized_visible_reference(match.group("value"))
        for match in SMART_TRADE_REFERENCE_RE.finditer(compacted)
        if _mailbox_normalized_visible_reference(match.group("value"))
    }


def _smart_contextual_reference_values_from_text(text: str, *, pattern: re.Pattern[str]) -> set[str]:
    compacted = compact_text(text)
    values: set[str] = set()
    for match in pattern.finditer(compacted):
        value = match.group(1) if match.groups() else match.group(0)
        normalized = _mailbox_normalized_visible_reference(value)
        if normalized:
            values.add(normalized)
    return values


def _smart_object_row_absorbing_base_row(object_row: SmartInboxRow, base_row: SmartInboxRow) -> SmartInboxRow:
    latest_row = _latest_smart_row(object_row, base_row)
    grouping_reason = dict(object_row.grouping_reason)
    absorbed = list(grouping_reason.get("absorbed_mailbox_rows") or [])
    absorbed.append(
        {
            "row_key": base_row.row_key,
            "row_type": base_row.row_type,
            "source": base_row.grouping_reason.get("source"),
            "ai_group_id": base_row.grouping_reason.get("ai_group_id"),
        }
    )
    grouping_reason["absorbed_mailbox_rows"] = absorbed
    return object_row.model_copy(
        update={
            "title": _smart_absorbed_object_title(object_row, base_row),
            "summary": _smart_absorbed_object_summary(object_row, base_row),
            "latest_message_at": latest_row.latest_message_at,
            "latest_message_id": latest_row.latest_message_id,
            "source_thread_ids": _dedupe_strings([*object_row.source_thread_ids, *base_row.source_thread_ids]),
            "source_message_ids": _dedupe_strings([*object_row.source_message_ids, *base_row.source_message_ids]),
            "confidence": max(object_row.confidence, base_row.confidence),
            "grouping_reason": grouping_reason,
            "readiness": "ready" if object_row.readiness == "ready" and base_row.readiness == "ready" else object_row.readiness,
            "priority": max(object_row.priority, base_row.priority),
        }
    )


def _latest_smart_row(left: SmartInboxRow, right: SmartInboxRow) -> SmartInboxRow:
    if (right.latest_message_at or "") > (left.latest_message_at or ""):
        return right
    return left


def _smart_absorbed_object_title(object_row: SmartInboxRow, base_row: SmartInboxRow) -> str:
    if not base_row.title:
        return object_row.title
    if _smart_object_title_is_generic(object_row) or len(base_row.title) > len(object_row.title) + 8:
        return base_row.title
    return object_row.title


def _smart_absorbed_object_summary(object_row: SmartInboxRow, base_row: SmartInboxRow) -> str:
    if _smart_object_is_trade_reference(object_row):
        return f"{object_row.title} combines the trade confirmation with the related bank/support validation thread."
    if not base_row.summary:
        return object_row.summary
    if _smart_object_summary_is_generic(object_row.summary) or len(base_row.summary) > len(object_row.summary) + 24:
        return base_row.summary
    return object_row.summary


def _smart_object_is_trade_reference(row: SmartInboxRow) -> bool:
    return str(row.grouping_reason.get("canonical_key") or "").startswith("trade_id:")


def _smart_object_title_is_generic(row: SmartInboxRow) -> bool:
    canonical_key = str(row.grouping_reason.get("canonical_key") or "")
    reference = canonical_key.rsplit(":", 1)[-1] if ":" in canonical_key else ""
    title = row.title.strip().lower()
    if reference and title.endswith(f" ticket {reference.lower()}"):
        return True
    return title.startswith("mail related to ")


def _smart_object_summary_is_generic(summary: str) -> bool:
    normalized = summary.strip().lower()
    return normalized.startswith("mail related to ") or re.match(r"^\d+ emails related to ", normalized) is not None


def _smart_workflow_cluster_rows(rows: list[SmartInboxRow]) -> list[SmartInboxRow]:
    buckets: dict[str, list[SmartInboxRow]] = defaultdict(list)
    for row in rows:
        cluster_key = _smart_workflow_cluster_key(row)
        if cluster_key:
            buckets[cluster_key].append(row)

    covered_row_ids: set[str] = set()
    clustered_rows: list[SmartInboxRow] = []
    for cluster_key, bucket_rows in buckets.items():
        source_thread_ids = {thread_id for row in bucket_rows for thread_id in row.source_thread_ids}
        if len(source_thread_ids) < 2 or not _smart_workflow_cluster_is_safe(cluster_key, bucket_rows):
            continue
        clustered_rows.append(_smart_row_from_workflow_cluster(cluster_key, bucket_rows))
        covered_row_ids.update(row.id for row in bucket_rows)

    if not clustered_rows:
        return rows
    return [*clustered_rows, *[row for row in rows if row.id not in covered_row_ids]]


def _smart_workflow_cluster_is_safe(cluster_key: str, rows: list[SmartInboxRow]) -> bool:
    if len(rows) < 2:
        return False
    platform, _category, object_key = _smart_workflow_cluster_parts(cluster_key)
    if platform in SMART_WORKFLOW_PLATFORM_STOP_WORDS:
        return False
    if not object_key:
        return False
    dates = [_parse_date(row.latest_message_at) for row in rows if row.latest_message_at]
    if len(dates) >= 2 and (max(dates) - min(dates)).days > SMART_WORKFLOW_MAX_SPAN_DAYS:
        return False
    return True


def _smart_workflow_cluster_key(row: SmartInboxRow) -> str | None:
    if row.row_type == "verified_group" and row.grouping_reason.get("source") == "mail_object":
        return None
    content_text = _smart_workflow_content_text(row)
    tokens = _smart_workflow_tokens(" ".join([content_text, row.primary_sender or ""]))
    if not tokens:
        return None
    category = _smart_workflow_category(tokens)
    if not category:
        return None
    platform = _smart_workflow_platform(tokens, category=category)
    if not platform:
        return None
    object_key = _smart_workflow_shared_object_key(row, category=category)
    if not object_key:
        return None
    return f"{platform}:{category}:object:{object_key}"


def _smart_workflow_cluster_parts(cluster_key: str) -> tuple[str, str, str]:
    parts = cluster_key.split(":", 2)
    platform = parts[0] if parts else ""
    category = parts[1] if len(parts) > 1 else ""
    object_key = parts[2] if len(parts) > 2 else ""
    if object_key.startswith("object:"):
        object_key = object_key.removeprefix("object:")
    return platform, category, object_key


def _smart_workflow_shared_object_key(row: SmartInboxRow, *, category: str) -> str | None:
    reason = row.grouping_reason if isinstance(row.grouping_reason, dict) else {}
    candidates = [
        reason.get("workflow_object"),
        reason.get("shared_object"),
        reason.get("reference"),
    ]
    metadata = reason.get("metadata") if isinstance(reason.get("metadata"), dict) else {}
    if isinstance(metadata, dict):
        candidates.extend([metadata.get("workflow_object"), metadata.get("shared_object"), metadata.get("reference")])
    for item in reason.get("visible_references") or []:
        candidates.append(item)

    for candidate in candidates:
        normalized = _smart_workflow_normalized_object_key(category, candidate)
        if normalized:
            return normalized
    return None


def _smart_workflow_normalized_object_key(category: str, candidate: Any) -> str | None:
    signal_name = ""
    value: Any = candidate
    if isinstance(candidate, dict):
        signal_name = compact_text(str(candidate.get("type") or candidate.get("signal_name") or candidate.get("kind") or ""))
        value = candidate.get("value") or candidate.get("id") or candidate.get("key") or candidate.get("reference")
    elif isinstance(candidate, (list, tuple, set)):
        return None
    normalized_value = compact_text(str(value or ""))
    if not normalized_value:
        return None
    normalized_value = re.sub(r"[^a-z0-9]+", "-", normalized_value.lower()).strip("-")
    if len(normalized_value) < 4:
        return None
    normalized_signal = re.sub(r"[^a-z0-9]+", "-", (signal_name or category).lower()).strip("-")
    return f"{normalized_signal}:{normalized_value}"


def _smart_workflow_content_text(row: SmartInboxRow) -> str:
    return " ".join(
        str(value or "")
        for value in [
            row.title,
            row.summary,
        ]
    ).lower()


def _smart_workflow_tokens(text: str) -> set[str]:
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", text)
        if len(token) > 1 and (token not in SMART_WORKFLOW_TOKEN_STOP_WORDS or token == "application")
    }
    expanded = set(tokens)
    for token in tokens:
        if token == "fx" or token.startswith("fx") or "fx" in token:
            expanded.add("fx")
        if "retail" in token:
            expanded.add("retail")
        if "direct" in token:
            expanded.add("direct")
        if token == "rbi" or token.startswith("rdg") or "rbi" in token:
            expanded.add("rbi")
    return expanded


def _smart_workflow_category(tokens: set[str]) -> str | None:
    if tokens & SMART_WORKFLOW_REMITTANCE_TOKENS:
        return "wire-remittance"
    if tokens & SMART_WORKFLOW_APPLICATION_TOKENS and {"admission", "admissions", "application", "admitted", "reconsideration", "cost", "costs", "aid"} & tokens:
        return "application-lifecycle"
    if tokens & SMART_WORKFLOW_LIMIT_TOKENS and ({"trading", "limit"} & tokens):
        return "trading-limit"
    if tokens & SMART_WORKFLOW_ACCOUNT_TOKENS:
        return "account-setup"
    return None


def _smart_workflow_platform(tokens: set[str], *, category: str) -> str | None:
    if "fx" in tokens and ("retail" in tokens or "trading" in tokens or "limit" in tokens):
        return "fx-retail"
    if "rbi" in tokens and ("direct" in tokens or "retail" in tokens):
        return "rbi-retail-direct"
    if category == "wire-remittance" and "hdfc" in tokens:
        return "hdfc-bank"
    if category == "application-lifecycle" and "penn" in tokens and "state" in tokens:
        return "penn-state"
    return None


def _smart_row_from_workflow_cluster(cluster_key: str, rows: list[SmartInboxRow]) -> SmartInboxRow:
    ordered_rows = sorted(rows, key=lambda row: row.latest_message_at or "", reverse=True)
    latest = ordered_rows[0]
    platform, category, object_key = _smart_workflow_cluster_parts(cluster_key)
    platform_title = _smart_workflow_platform_title(platform)
    title = _smart_workflow_cluster_title(platform, category, ordered_rows)
    source_message_ids = _dedupe_strings([message_id for row in ordered_rows for message_id in row.source_message_ids])
    source_thread_ids = _dedupe_strings([thread_id for row in ordered_rows for thread_id in row.source_thread_ids])
    related_count = len(source_message_ids) or len(source_thread_ids) or len(ordered_rows)
    related_label = "email" if related_count == 1 else "emails"
    return SmartInboxRow(
        id=f"smart-workflow:{hashlib.sha1(cluster_key.encode('utf-8')).hexdigest()[:20]}",
        row_key=f"smart-workflow:{cluster_key}",
        row_type="related_bundle",
        title=title,
        summary=f"{related_count} related {related_label} for {title}.",
        primary_sender=platform_title or latest.primary_sender,
        latest_message_at=latest.latest_message_at,
        latest_message_id=latest.latest_message_id,
        source_thread_ids=source_thread_ids,
        source_message_ids=source_message_ids,
        confidence_tier="medium",
        confidence=0.78,
        grouping_reason={
            "source": "smart_workflow_cluster",
            "cluster_key": cluster_key,
            "shared_object_key": object_key,
            "member_count": len(ordered_rows),
            "source_row_ids": [row.id for row in ordered_rows],
        },
        offline_status="partial",
        readiness="ready" if all(row.readiness == "ready" for row in ordered_rows) else "partial",
        action_type=latest.action_type,
        priority=max((row.priority for row in ordered_rows), default=latest.priority),
    )


def _smart_workflow_cluster_title(platform: str, category: str, rows: list[SmartInboxRow]) -> str:
    platform_title = _smart_workflow_platform_title(platform)
    if category == "trading-limit":
        return _smart_workflow_trading_limit_title(platform_title, rows)
    if category == "account-setup":
        return f"{platform_title} account setup"
    if category == "wire-remittance":
        outcome_title = _smart_workflow_remittance_outcome_title(platform_title, rows)
        if outcome_title:
            return outcome_title
        return _smart_workflow_remittance_fallback_title(platform_title, rows)
    if category == "application-lifecycle":
        lifecycle_title = _smart_workflow_application_lifecycle_title(platform_title, rows)
        if lifecycle_title:
            return lifecycle_title
        return f"{platform_title} application timeline"
    return f"{platform_title} related mail"


def _smart_workflow_trading_limit_title(platform_title: str, rows: list[SmartInboxRow]) -> str:
    text = " ".join(_smart_workflow_content_text(row) for row in rows)
    if re.search(r"(?i)\b(?:fund|funding|funded|deposit|balance)\b", text) and re.search(r"(?i)\b(?:confirm|needed|required|once)\b", text):
        return f"{platform_title} trading limit funding needed"
    if re.search(r"(?i)\b(?:reject|rejected|declined|denied)\b", text):
        return f"{platform_title} trading limit rejected"
    if re.search(r"(?i)\b(?:approved|enabled|set up|activated)\b", text):
        return f"{platform_title} trading limit approved"
    if re.search(r"(?i)\b(?:forwarded|sent to the bank|under review|approval)\b", text):
        return f"{platform_title} trading limit under review"
    return f"{platform_title} trading limit"


def _smart_workflow_remittance_outcome_title(platform_title: str, rows: list[SmartInboxRow]) -> str | None:
    for row in rows:
        title = compact_text(row.title)
        if not title:
            continue
        if not re.search(
            r"(?i)\b(?:(?:outward\s+)?remittance|wire(?:\s+deposit|\s+transfer)?)\s+(?:processed|completed|received)\b",
            title,
        ):
            continue
        if platform_title.lower() in title.lower():
            return title
        return f"{platform_title} {_lowercase_initial(title)}"
    return None


def _smart_workflow_remittance_fallback_title(platform_title: str, rows: list[SmartInboxRow]) -> str:
    text = " ".join(_smart_workflow_content_text(row) for row in rows)
    if re.search(r"(?i)\b(?:acknowledged|registered|received your|has been received)\b", text):
        return f"{platform_title} wire transfer acknowledged"
    if re.search(r"(?i)\b(?:under review|in progress|checking|working on it)\b", text):
        return f"{platform_title} wire transfer under review"
    if re.search(r"(?i)\b(?:will respond|we'?ll respond|resolution by|expected update)\b", text):
        return f"{platform_title} wire transfer awaiting response"
    return f"{platform_title} wire transfer"


def _lowercase_initial(value: str) -> str:
    if not value:
        return value
    return f"{value[0].lower()}{value[1:]}"


def _smart_workflow_application_lifecycle_title(platform_title: str, rows: list[SmartInboxRow]) -> str | None:
    text = " ".join(_smart_workflow_content_text(row) for row in rows)
    labels: list[str] = []
    if re.search(r"(?i)\b(?:admission|admissions|admitted|acceptance|accepted|welcome|next\s+steps?)\b", text):
        labels.append("admission")
    if re.search(r"(?i)\b(?:costs?|estimated\s+costs?|financial\s+aid|aid\s+offer|scholarships?)\b", text):
        labels.append("costs")
    if re.search(r"(?i)\breconsideration\b", text):
        labels.append("reconsideration")
    if len(labels) >= 2:
        return f"{platform_title} {_natural_join(labels[:3])}"
    if len(labels) == 1:
        if labels[0] == "admission" and re.search(r"(?i)\b(?:admitted|accepted|acceptance|next\s+steps?)\b", text):
            return f"{platform_title} admission next steps"
        if labels[0] == "costs" and re.search(r"(?i)\b(?:available|ready|offer)\b", text):
            return f"{platform_title} estimated costs available"
        if labels[0] == "reconsideration" and re.search(r"(?i)\b(?:answered|replied|response|reply)\b", text):
            return f"{platform_title} reconsideration reply"
        return f"{platform_title} {labels[0]}"
    return None


def _smart_workflow_platform_title(platform: str) -> str:
    return " ".join(
        DOMAIN_WORD_TITLES.get(word, word.title())
        for word in platform.split("-")
        if word
    )


def _smart_inbox_response_from_rows(rows: list[SmartInboxRow], *, generated_at: str | None) -> SmartInboxResponse:
    rows_with_reader_targets = [_smart_row_with_reader_target(row) for row in rows]
    sections = _bucket_smart_rows(rows_with_reader_targets)
    ready_count = sum(1 for row in rows_with_reader_targets if row.readiness == "ready")
    failed_count = sum(1 for row in rows_with_reader_targets if row.readiness == "failed")
    partial_count = max(0, len(rows_with_reader_targets) - ready_count - failed_count)
    return SmartInboxResponse(
        total_rows=len(rows_with_reader_targets),
        sections=sections,
        related_suggestions=_smart_related_suggestions(rows),
        ready_count=ready_count,
        partial_count=partial_count,
        failed_count=failed_count,
        generated_at=generated_at,
        hot_window_days=SMART_HOT_WINDOW_DAYS,
        hot_window_message_cap=SMART_HOT_WINDOW_MESSAGE_CAP,
        hot_window_thread_cap=SMART_HOT_WINDOW_THREAD_CAP,
    )


def _smart_row_with_reader_target(row: SmartInboxRow) -> SmartInboxRow:
    if row.reader_thread_id:
        return row
    reader_thread_id = _smart_row_reader_thread_id(row)
    if reader_thread_id == row.reader_thread_id:
        return row
    return row.model_copy(update={"reader_thread_id": reader_thread_id})


def _smart_row_reader_thread_id(row: SmartInboxRow) -> str | None:
    source_threads = [thread_id for thread_id in row.source_thread_ids if thread_id]
    source_messages = [message_id for message_id in row.source_message_ids if message_id]
    if len(source_threads) == 1:
        return source_threads[0]
    is_task_group = (
        row.row_type in {"verified_group", "related_bundle"}
        or len(source_threads) > 1
        or len(source_messages) > 1
        or row.row_key.startswith(MAILBOX_DISPLAY_CLUSTER_PREFIX)
    )
    if is_task_group:
        return f"{SMART_ROW_READER_PREFIX}{row.id}"
    if source_threads:
        return source_threads[0]
    return row.row_key or None


def _smart_hot_window_rows(rows: list[SmartInboxRow]) -> list[SmartInboxRow]:
    cutoff = datetime.now().astimezone() - timedelta(days=SMART_HOT_WINDOW_DAYS)
    recent_rows = [
        row
        for row in rows
        if _smart_row_in_hot_window(row, cutoff=cutoff)
    ]
    limited: list[SmartInboxRow] = []
    message_count = 0
    thread_cap = SMART_HOT_WINDOW_THREAD_CAP if SMART_HOT_WINDOW_THREAD_CAP > 0 else len(recent_rows)
    message_cap = SMART_HOT_WINDOW_MESSAGE_CAP if SMART_HOT_WINDOW_MESSAGE_CAP > 0 else None
    for row in recent_rows[:thread_cap]:
        row_message_count = _smart_row_message_count(row)
        if message_cap is not None and limited and message_count + row_message_count > message_cap:
            break
        limited.append(row)
        message_count += row_message_count
        if message_cap is not None and message_count >= message_cap:
            break
    return limited


def _smart_row_in_hot_window(row: SmartInboxRow, *, cutoff: datetime) -> bool:
    if not row.latest_message_at:
        return True
    return _parse_date(row.latest_message_at) >= cutoff


def _smart_row_message_count(row: SmartInboxRow) -> int:
    reason_count = row.grouping_reason.get("message_count") if isinstance(row.grouping_reason, dict) else None
    if isinstance(reason_count, int) and reason_count > 0:
        return reason_count
    try:
        parsed = int(reason_count)
        if parsed > 0:
            return parsed
    except (TypeError, ValueError):
        pass
    return max(1, len(row.source_message_ids))


def _smart_processed_message_count(rows: list[SmartInboxRow]) -> int:
    unique_message_ids: set[str] = set()
    fallback_count = 0
    for row in rows:
        if row.source_message_ids:
            unique_message_ids.update(row.source_message_ids)
        else:
            fallback_count += _smart_row_message_count(row)
    return len(unique_message_ids) + fallback_count


def _smart_processed_thread_count(rows: list[SmartInboxRow], mailbox: MailboxResponse) -> int:
    unique_thread_ids: set[str] = set()
    fallback_rows = 0
    for row in rows:
        if row.source_thread_ids:
            unique_thread_ids.update(row.source_thread_ids)
        elif row.row_key:
            unique_thread_ids.add(row.row_key)
        else:
            fallback_rows += 1
    return max(len(unique_thread_ids) + fallback_rows, len(rows))


def _smart_first_ready_target_threads(
    *,
    mailbox: MailboxResponse,
    sync: AppSessionSyncState,
    processed_threads: int,
) -> int:
    default_target = min(SMART_FIRST_READY_TARGET_MESSAGES, mailbox.total_threads or SMART_FIRST_READY_TARGET_MESSAGES)
    if processed_threads > 0 and (sync.hot_window_completed_at or sync.full_import_completed):
        return min(default_target, processed_threads)
    return default_target


def _bucket_smart_rows(rows: list[SmartInboxRow]) -> list[SmartInboxSection]:
    reference = datetime.now().astimezone().date()
    buckets: OrderedDict[str, tuple[str, list[SmartInboxRow]]] = OrderedDict()
    for row in rows:
        date = _parse_date(row.latest_message_at or datetime.now(timezone.utc).isoformat()).date()
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
    return [SmartInboxSection(id=key, title=title, rows=items) for key, (title, items) in buckets.items()]


def _smart_related_suggestions(rows: list[SmartInboxRow], *, limit: int = 50) -> list[SmartRelatedSuggestion]:
    return []


def _smart_row_from_mailbox_row(row: GmailThreadRow, *, section_id: str) -> SmartInboxRow:
    readiness = _smart_row_readiness(row)
    row_type = _smart_row_type(row)
    confidence_tier = "unknown"
    confidence = 0.0
    if row_type == "verified_group":
        confidence_tier = "strong"
        confidence = 0.9
        if _smart_mailbox_projection_has_direct_reference(row):
            confidence_tier = "exact"
            confidence = 0.96
    elif row_type == "summarized_thread":
        confidence_tier = "strong" if readiness == "ready" else "medium"
        confidence = 0.82 if readiness == "ready" else 0.62
    elif row_type == "related_bundle":
        confidence_tier = "medium"
        confidence = 0.7
    elif row_type == "update":
        confidence_tier = "weak"
        confidence = 0.5
    title = row.ai_title or row.title or row.latest_subject or "Untitled email"
    summary = row.ai_summary or row.summary or row.snippet or ""
    source_thread_ids = _smart_row_source_thread_ids(row)
    source_message_ids = _dedupe_strings(
        [row.latest_source_record_id] + [child.message_id for child in row.children if child.message_id]
    )
    grouping_reason = {
        "source": "mailbox_projection",
        "section_id": section_id,
        "ai_group_id": row.ai_group_id,
        "message_count": row.message_count,
        "presentation_status": row.presentation_status,
        "enrichment_status": row.enrichment_status,
    }
    if row.grouping_metadata:
        grouping_reason["metadata"] = row.grouping_metadata
    visible_references = _smart_mailbox_row_visible_references(row)
    if visible_references:
        grouping_reason["visible_references"] = visible_references
    return SmartInboxRow(
        id=_smart_row_id(row.thread_id),
        row_key=row.thread_id,
        row_type=row_type,  # type: ignore[arg-type]
        title=title,
        summary=summary,
        primary_sender=row.sender or row.latest_sender,
        latest_message_at=row.latest_message_at or row.latest_received_at,
        latest_message_id=row.latest_source_record_id,
        source_thread_ids=source_thread_ids,
        source_message_ids=source_message_ids,
        confidence_tier=confidence_tier,  # type: ignore[arg-type]
        confidence=confidence,
        grouping_reason=grouping_reason,
        offline_status="partial",
        readiness=readiness,  # type: ignore[arg-type]
        action_type=row.action_type_key or row.action_type,
        priority=row.priority,
    )


def _smart_row_type(row: GmailThreadRow) -> str:
    if row.thread_id.startswith(MAILBOX_DISPLAY_CLUSTER_PREFIX):
        if _smart_mailbox_projection_has_reference(row):
            return "verified_group"
        return "related_bundle"
    if row.action_needed and row.current_state == "waiting":
        return "waiting"
    if row.ai_group_id:
        return "verified_group" if row.message_count > 1 else "summarized_thread"
    if row.presentation_status == "ai_ready":
        return "summarized_thread"
    if "CATEGORY_UPDATES" in row.label_ids or "CATEGORY_PROMOTIONS" in row.label_ids:
        return "update"
    return "normal"


def _smart_mailbox_projection_has_reference(row: GmailThreadRow) -> bool:
    metadata = row.grouping_metadata if isinstance(row.grouping_metadata, dict) else {}
    reference = metadata.get("reference") if isinstance(metadata.get("reference"), dict) else {}
    return bool(reference.get("value") and reference.get("signal_name"))


def _smart_mailbox_projection_has_direct_reference(row: GmailThreadRow) -> bool:
    metadata = row.grouping_metadata if isinstance(row.grouping_metadata, dict) else {}
    reference = metadata.get("reference") if isinstance(metadata.get("reference"), dict) else {}
    direct_count = int(reference.get("direct_message_count") or 0)
    total_count = int(reference.get("message_count") or 0)
    return bool(reference.get("value") and total_count > 0 and direct_count >= total_count)


def _smart_row_readiness(row: GmailThreadRow) -> str:
    if row.enrichment_status == "failed":
        return "failed"
    if row.presentation_status == "ai_ready":
        return "ready"
    return "partial"


def _smart_row_source_thread_ids(row: GmailThreadRow) -> list[str]:
    child_thread_ids = [child.gmail_thread_id for child in row.children if child.gmail_thread_id]
    if child_thread_ids:
        return _dedupe_strings(child_thread_ids)
    if row.thread_id.startswith(MAILBOX_DISPLAY_CLUSTER_PREFIX):
        return []
    return [row.thread_id]


def _smart_row_id(thread_id: str) -> str:
    return f"smart-row:{thread_id}"


def _dedupe_strings(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _smart_work_queue_from_snapshot(
    payload: dict[str, Any],
    *,
    dashboard: DashboardResponse,
    smart_inbox: SmartInboxResponse,
) -> SmartWorkQueueResponse:
    snapshot = SmartWorkQueueResponse.model_validate(payload or {})
    has_items = any(
        [
            snapshot.needs_action,
            snapshot.waiting,
            snapshot.active_conversations,
            snapshot.important_updates,
            snapshot.manual_reminders,
        ]
    )
    if has_items and not _smart_work_queue_is_dashboard_derived(snapshot):
        return snapshot
    if smart_inbox.total_rows > 0 or any(section.rows for section in smart_inbox.sections):
        return _smart_work_queue_from_smart_inbox(smart_inbox)
    return _smart_work_queue_from_dashboard(dashboard)


def _smart_work_queue_is_dashboard_derived(queue: SmartWorkQueueResponse) -> bool:
    items = [
        *queue.needs_action,
        *queue.waiting,
        *queue.active_conversations,
        *queue.important_updates,
        *queue.manual_reminders,
    ]
    return bool(items) and all(item.reason.get("source") == "dashboard_feed" for item in items)


def _smart_work_queue_from_smart_inbox(smart_inbox: SmartInboxResponse) -> SmartWorkQueueResponse:
    needs_action: list[SmartWorkItem] = []
    waiting: list[SmartWorkItem] = []
    active_conversations: list[SmartWorkItem] = []
    important_updates: list[SmartWorkItem] = []
    manual_reminders: list[SmartWorkItem] = []
    rows = sorted(
        [row for section in smart_inbox.sections for row in section.rows],
        key=lambda row: (row.priority, row.latest_message_at or ""),
        reverse=True,
    )
    for row in rows:
        kind = _smart_work_item_kind_from_row(row)
        if not kind:
            continue
        work_item = _smart_work_item_from_smart_row(row, kind=kind)
        if kind == "needs_action":
            needs_action.append(work_item)
        elif kind == "waiting":
            waiting.append(work_item)
        elif kind == "active_conversation":
            active_conversations.append(work_item)
        elif kind == "important_update":
            important_updates.append(work_item)
    return SmartWorkQueueResponse(
        needs_action=needs_action[:50],
        waiting=waiting[:50],
        active_conversations=active_conversations[:50],
        important_updates=important_updates[:50],
        manual_reminders=manual_reminders,
        total_open=len(needs_action[:50]) + len(waiting[:50]) + len(active_conversations[:50]) + len(important_updates[:50]) + len(manual_reminders),
    )


def _smart_work_item_kind_from_row(row: SmartInboxRow) -> str | None:
    if row.row_type == "waiting":
        return "waiting"
    if row.action_type in {"pay", "reply", "confirm", "review"} and row.priority >= 50:
        return "needs_action"
    if row.row_type == "verified_group" and row.priority >= 70:
        return "important_update"
    if row.row_type == "verified_group" and len(row.source_thread_ids) > 1 and row.priority >= 40:
        return "active_conversation"
    if row.action_type == "open" and row.priority >= 80 and row.row_type in {"summarized_thread", "update", "normal"}:
        return "important_update"
    return None


def _smart_work_item_from_smart_row(row: SmartInboxRow, *, kind: str) -> SmartWorkItem:
    return SmartWorkItem(
        id=f"smart-work-row:{hashlib.sha1(row.id.encode('utf-8')).hexdigest()[:20]}",
        kind=kind,  # type: ignore[arg-type]
        title=row.title,
        summary=row.summary,
        status="open",
        smart_row_id=row.id,
        source_thread_ids=row.source_thread_ids,
        source_message_ids=row.source_message_ids,
        due_at=None,
        priority=row.priority,
        confidence=row.confidence,
        reason={
            "source": "smart_inbox_row",
            "row_type": row.row_type,
            "action_type": row.action_type,
            "confidence_tier": row.confidence_tier,
        },
        created_at=row.latest_message_at,
        updated_at=row.latest_message_at,
    )


def _smart_work_queue_from_dashboard(dashboard: DashboardResponse) -> SmartWorkQueueResponse:
    needs_action: list[SmartWorkItem] = []
    waiting: list[SmartWorkItem] = []
    active_conversations: list[SmartWorkItem] = []
    important_updates: list[SmartWorkItem] = []
    manual_reminders: list[SmartWorkItem] = []
    for section_id, items in (
        ("now", dashboard.feed.now),
        ("today", dashboard.feed.today),
        ("worth_knowing", dashboard.feed.worth_knowing),
    ):
        for item in items:
            kind = _smart_work_item_kind(item, section_id=section_id)
            work_item = _smart_work_item_from_attention_item(item, kind=kind, section_id=section_id)
            if kind == "manual_reminder":
                manual_reminders.append(work_item)
            elif kind == "waiting":
                waiting.append(work_item)
            elif kind == "active_conversation":
                active_conversations.append(work_item)
            elif kind == "important_update":
                important_updates.append(work_item)
            else:
                needs_action.append(work_item)
    return SmartWorkQueueResponse(
        needs_action=needs_action,
        waiting=waiting,
        active_conversations=active_conversations,
        important_updates=important_updates,
        manual_reminders=manual_reminders,
        total_open=len(needs_action) + len(waiting) + len(active_conversations) + len(important_updates) + len(manual_reminders),
    )


def _smart_work_item_kind(item: AttentionItem, *, section_id: str) -> str:
    if item.source == "manual":
        return "manual_reminder"
    if item.current_state == "waiting":
        return "waiting"
    if item.need_type == "decision" or item.action_type != "none":
        return "needs_action"
    if section_id == "worth_knowing":
        return "important_update"
    return "active_conversation"


def _smart_work_item_from_attention_item(item: AttentionItem, *, kind: str, section_id: str) -> SmartWorkItem:
    source_thread_ids = [item.gmail_thread_id] if item.gmail_thread_id else []
    return SmartWorkItem(
        id=f"smart-work:{item.id}",
        kind=kind,  # type: ignore[arg-type]
        title=item.title,
        summary=item.why_this_is_here,
        status="open",
        smart_row_id=_smart_row_id(item.gmail_thread_id) if item.gmail_thread_id else None,
        source_thread_ids=source_thread_ids,
        source_message_ids=[],
        due_at=item.due_at,
        priority=_smart_work_priority(item),
        confidence=_smart_work_confidence(item),
        reason={
            "source": "dashboard_feed",
            "section_id": section_id,
            "need_type": item.need_type,
            "timing_band": item.timing_band,
            "action_confidence": item.action_confidence,
            "trace_id": item.trace_id,
        },
        created_at=item.created_at,
        updated_at=item.created_at,
    )


def _smart_inbox_storage_rows(smart_inbox: SmartInboxResponse, *, user_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for section in smart_inbox.sections:
        for row in section.rows:
            payload = row.model_dump(mode="json")
            payload["id"] = _smart_storage_id(user_id, row.id)
            payload["section"] = section.id
            payload["status"] = "active"
            rows.append(payload)
    return rows


def _smart_work_storage_items(smart_work_queue: SmartWorkQueueResponse, *, user_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for work_items in (
        smart_work_queue.needs_action,
        smart_work_queue.waiting,
        smart_work_queue.active_conversations,
        smart_work_queue.important_updates,
        smart_work_queue.manual_reminders,
    ):
        for item in work_items:
            payload = item.model_dump(mode="json")
            payload["id"] = _smart_storage_id(user_id, item.id)
            if item.smart_row_id:
                payload["smart_row_id"] = _smart_storage_id(user_id, item.smart_row_id)
            items.append(payload)
    return items


def _smart_related_storage_suggestions(smart_inbox: SmartInboxResponse, *, user_id: str) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    for suggestion in smart_inbox.related_suggestions:
        payload = suggestion.model_dump(mode="json")
        payload["id"] = _smart_storage_id(user_id, suggestion.id)
        payload["source_row_id"] = _smart_storage_id(user_id, suggestion.source_row_id)
        payload["related_row_id"] = _smart_storage_id(user_id, suggestion.related_row_id)
        suggestions.append(payload)
    return suggestions


def _smart_storage_id(user_id: str, public_id: str) -> str:
    return f"{user_id}:{public_id}"


def _smart_work_priority(item: AttentionItem) -> int:
    base = {"now": 80, "today": 60, "later": 35, "hidden": 10}.get(item.timing_band, 35)
    importance_bonus = {"high": 20, "medium": 10, "low": 0}.get(item.importance_level or "low", 0)
    decision_bonus = 10 if item.need_type == "decision" else 0
    return min(100, base + importance_bonus + decision_bonus)


def _smart_work_confidence(item: AttentionItem) -> float:
    return {"high": 0.9, "medium": 0.72, "low": 0.5}.get(item.action_confidence, 0.5)


def _smart_readiness_from_snapshot(
    payload: dict[str, Any],
    *,
    mailbox: MailboxResponse,
    sync: AppSessionSyncState,
    smart_inbox: SmartInboxResponse,
) -> SmartReadinessResponse:
    snapshot = SmartReadinessResponse.model_validate(payload or {})
    derived = _smart_readiness_from_mailbox(mailbox=mailbox, sync=sync, smart_inbox=smart_inbox)
    if snapshot.offline_ready and derived.hot_window_complete:
        return derived.model_copy(update={"offline_ready": True, "stage": "offline_ready"})
    return derived


def _smart_readiness_from_mailbox(
    *,
    mailbox: MailboxResponse,
    sync: AppSessionSyncState,
    smart_inbox: SmartInboxResponse,
) -> SmartReadinessResponse:
    smart_rows = [row for section in smart_inbox.sections for row in section.rows]
    processed_messages = _smart_processed_message_count(smart_rows)
    processed_threads = _smart_processed_thread_count(smart_rows, mailbox)
    offline_ready_rows, offline_partial_rows, offline_failed_rows = _smart_offline_counts(smart_inbox)
    hot_window_rows_ready = bool(smart_inbox.total_rows > 0 and smart_inbox.ready_count >= smart_inbox.total_rows and smart_inbox.partial_count == 0 and smart_inbox.failed_count == 0)
    target_threads = _smart_first_ready_target_threads(
        mailbox=mailbox,
        sync=sync,
        processed_threads=processed_threads,
    )
    first_ready_complete = bool(target_threads > 0 and processed_threads >= target_threads and hot_window_rows_ready)
    hot_window_import_completed = bool(sync.hot_window_completed_at or sync.full_import_completed)
    hot_window_target_reached = bool(hot_window_import_completed and hot_window_rows_ready)
    hot_window_complete = bool(
        hot_window_import_completed
        and sync.enrichment_pending_count == 0
        and smart_inbox.total_rows > 0
        and hot_window_target_reached
    )
    offline_ready = bool(
        hot_window_complete
        and smart_inbox.total_rows > 0
        and offline_ready_rows >= smart_inbox.total_rows
        and offline_partial_rows == 0
        and offline_failed_rows == 0
    )
    if sync.last_error and smart_inbox.ready_count == 0:
        stage = "failed"
    elif smart_inbox.total_rows == 0:
        stage = "syncing" if sync.full_import_running else "empty"
    elif sync.enrichment_pending_count > 0 or smart_inbox.partial_count > 0:
        stage = "classifying"
    elif hot_window_complete and offline_ready:
        stage = "offline_ready"
    else:
        stage = "snapshot_ready"
    return SmartReadinessResponse(
        stage=stage,  # type: ignore[arg-type]
        first_ready_complete=first_ready_complete,
        hot_window_complete=hot_window_complete,
        offline_ready=offline_ready,
        first_ready_target_messages=SMART_FIRST_READY_TARGET_MESSAGES,
        hot_window_message_cap=SMART_HOT_WINDOW_MESSAGE_CAP,
        hot_window_thread_cap=SMART_HOT_WINDOW_THREAD_CAP,
        processed_messages=processed_messages,
        processed_threads=processed_threads,
        ready_rows=smart_inbox.ready_count,
        partial_rows=smart_inbox.partial_count,
        failed_rows=smart_inbox.failed_count,
        offline_ready_rows=offline_ready_rows,
        offline_partial_rows=offline_partial_rows,
        offline_failed_rows=offline_failed_rows,
        last_error=sync.last_error,
    )


def _smart_offline_counts(smart_inbox: SmartInboxResponse) -> tuple[int, int, int]:
    rows = [row for section in smart_inbox.sections for row in section.rows]
    ready = sum(1 for row in rows if row.offline_status == "ready")
    failed = sum(1 for row in rows if row.offline_status == "failed")
    partial = max(0, len(rows) - ready - failed)
    return ready, partial, failed


def refresh_app_session_snapshot(settings: Settings, *, user_id: str, include_dashboard: bool = True) -> AppSessionResponse | None:
    from app.db.repository import get_user
    from app.services.auth import CurrentUser

    stored = get_user(str(settings.database_path), user_id)
    if stored is None:
        return None
    user = CurrentUser(id=stored.id, email=stored.email, display_name=stored.display_name)
    database_url = str(settings.database_path)
    auth = _google_auth_state(settings, user_id=user.id)
    mailbox = build_mailbox_response(settings, user_id=user.id, label="inbox", limit=APP_SESSION_MAILBOX_LIMIT)
    _enqueue_body_fetch_for_mailbox_rows(settings, user_id=user.id, mailbox=mailbox, limit=BODY_WARMUP_GROUP_LIMIT, priority=70)
    status_counts = count_mail_groups_by_enrichment_status(database_url, user_id=user.id)
    last_ai_error = latest_mail_group_ai_error(database_url, user_id=user.id)
    dashboard = (
        build_dashboard_response(settings, user_id=user.id, auth=auth, profile=user.profile)
        if include_dashboard
        else DashboardResponse(
            auth=auth,
            profile=user.profile,
            feed=FeedResponse(),
            runtime_status={
                "feed_source": "deferred",
                **_mail_runtime_status(database_url, status_counts=status_counts, last_ai_error=last_ai_error),
            },
        )
    )
    state = get_import_state(database_url, user_id=user.id)
    full_backfill_completed_at = getattr(state, "full_backfill_completed_at", None) if state is not None else None
    hot_window_started_at = getattr(state, "hot_window_started_at", None) if state is not None else None
    hot_window_completed_at = getattr(state, "hot_window_completed_at", None) if state is not None else None
    sync = AppSessionSyncState(
        last_sync_at=state.last_import_completed_at if state else None,
        last_error=(state.last_sync_error if state else None) or (last_ai_error if status_counts.get("ready", 0) == 0 else None),
        enrichment_pending_count=status_counts.get("pending", 0),
        ready_group_count=status_counts.get("ready", 0),
        oldest_imported_at=oldest_imported_message_at(database_url, user_id=user.id),
        full_import_running=mailbox.full_import_running,
        full_import_completed=bool(full_backfill_completed_at),
        full_import_completed_at=full_backfill_completed_at,
        hot_window_started_at=hot_window_started_at,
        hot_window_completed_at=hot_window_completed_at,
        pending_action_count=count_pending_thread_actions(database_url, user_id=user.id),
        last_ai_error=last_ai_error,
    )
    smart_inbox = _smart_inbox_from_mailbox_and_objects(database_url, user_id=user.id, mailbox=mailbox)
    smart_inbox = _smart_inbox_with_offline_status(database_url, user_id=user.id, smart_inbox=smart_inbox)
    _enqueue_body_fetch_for_smart_inbox(
        settings,
        user_id=user.id,
        smart_inbox=smart_inbox,
        limit=SMART_OFFLINE_WARMUP_THREAD_LIMIT,
        priority=65,
    )
    smart_work_queue = _smart_work_queue_from_smart_inbox(smart_inbox)
    smart_readiness = _smart_readiness_from_mailbox(mailbox=mailbox, sync=sync, smart_inbox=smart_inbox)
    replace_smart_inbox_projection(
        database_url,
        user_id=user.id,
        rows=_smart_inbox_storage_rows(smart_inbox, user_id=user.id),
        related_suggestions=_smart_related_storage_suggestions(smart_inbox, user_id=user.id),
        work_items=_smart_work_storage_items(smart_work_queue, user_id=user.id),
    )
    upsert_app_session_snapshot(
        database_url,
        user_id=user.id,
        dashboard=dashboard.model_dump(mode="json"),
        mailbox=mailbox.model_dump(mode="json"),
        sync={**sync.model_dump(mode="json"), "projection_version": APP_SESSION_PROJECTION_VERSION},
        smart_inbox=smart_inbox.model_dump(mode="json"),
        smart_work_queue=smart_work_queue.model_dump(mode="json"),
        smart_readiness=smart_readiness.model_dump(mode="json"),
    )
    return build_app_session_response(settings, user=user)


def refresh_visible_mail_projection(settings: Settings, *, user_id: str, include_ai_lifecycle: bool = True) -> int:
    database_url = str(settings.database_path)
    if include_ai_lifecycle:
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
    if not getattr(settings, "openai_configured", False):
        return 0
    database_url = str(settings.database_path)
    since = (datetime.now(timezone.utc) - timedelta(days=AI_LIFECYCLE_PROJECTION_DAYS)).isoformat()
    messages = list_recent_messages_since(
        database_url,
        user_id=user_id,
        since_iso=since,
        limit=AI_LIFECYCLE_PROJECTION_MESSAGE_LIMIT,
    )
    candidate_messages = _ai_lifecycle_candidate_messages(messages)
    if len(candidate_messages) < 2:
        return 0
    try:
        proposals = _ai_lifecycle_group_proposals(settings, messages=candidate_messages)
    except Exception as exc:
        logger.warning("AI lifecycle grouping failed for user %s: %s", user_id, exc)
        return 0
    return _store_ai_lifecycle_groups(settings, user_id=user_id, messages=candidate_messages, grouped_outputs=proposals)


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


def enqueue_app_session_snapshot_refresh(settings: Settings, *, user_id: str, priority: int = 10) -> str:
    job = enqueue_job(
        str(settings.database_path),
        kind="app_session_snapshot_refresh",
        queue="default",
        user_id=user_id,
        dedupe_key=f"app-session-snapshot:{user_id}",
        priority=priority,
        payload={"user_id": user_id},
    )
    return job.id


def _enqueue_hot_window_mail_group_enrich(settings: Settings, *, user_id: str, priority: int = 96) -> str:
    job = enqueue_job(
        str(settings.database_path),
        kind="mail_group_enrich",
        queue="critical",
        user_id=user_id,
        dedupe_key=f"first-run-mail-group-enrich:{user_id}",
        priority=priority,
        payload={"user_id": user_id, "source": "first_run_hot_window"},
    )
    return job.id


def hot_window_product_ready(
    settings: Settings,
    *,
    user_id: str,
    state=None,
    recent_since: str | None = None,
    status_counts: dict[str, int] | None = None,
    mailbox: MailboxResponse | None = None,
) -> bool:
    database_url = str(settings.database_path)
    state = state if state is not None else get_import_state(database_url, user_id=user_id)
    hot_window_import_ready = _hot_window_import_ready_from_state(state)
    if not hot_window_import_ready:
        return False
    since_iso = recent_since or (datetime.now(timezone.utc) - timedelta(days=_recent_visible_days(settings))).isoformat()
    counts_ready = _hot_window_product_ready_from_status_counts(
        hot_window_import_ready=hot_window_import_ready,
        status_counts=status_counts
        if status_counts is not None
        else count_mail_groups_by_enrichment_status_since(
            database_url,
            user_id=user_id,
            since_iso=since_iso,
            mailbox_label="inbox",
        ),
    )
    if not counts_ready:
        return False
    hot_mailbox = mailbox or build_mailbox_response(settings, user_id=user_id, label="inbox", limit=APP_SESSION_MAILBOX_LIMIT)
    return _hot_window_product_ready_from_mailbox(hot_mailbox)


def _hot_window_import_ready_from_state(state) -> bool:
    return bool(
        state
        and (
            getattr(state, "hot_window_completed_at", None)
            or getattr(state, "full_backfill_completed_at", None)
        )
    )


def _smart_inbox_product_ready(*, state, smart_inbox: SmartInboxResponse | None) -> bool:
    if not _hot_window_import_ready_from_state(state) or smart_inbox is None:
        return False
    return smart_inbox_response_is_product_ready(smart_inbox)


def _smart_inbox_has_rows(smart_inbox: SmartInboxResponse | None) -> bool:
    return bool(smart_inbox and (smart_inbox.total_rows > 0 or any(section.rows for section in smart_inbox.sections)))


def _hot_window_product_ready_from_status_counts(*, hot_window_import_ready: bool, status_counts: dict[str, int]) -> bool:
    return bool(
        hot_window_import_ready
        and status_counts.get("ready", 0) > 0
    )


def _hot_window_product_ready_from_mailbox(mailbox: MailboxResponse) -> bool:
    report = audit_smart_inbox_quality(mailbox)
    if report.total_rows == 0:
        return False
    if report.blocking_issue_count > 0:
        blocking_codes = sorted({issue.code for issue in report.issues if issue.severity == "blocker"})
        logger.info(
            "Hot window inbox quality is not ready: %s blocking issues across %s rows (%s)",
            report.blocking_issue_count,
            report.total_rows,
            ", ".join(blocking_codes),
        )
        return False
    return True


def _mail_runtime_status(database_url: str, *, status_counts: dict[str, int], last_ai_error: str | None) -> dict[str, Any]:
    queue_health = get_queue_health(database_url)
    ready_count = status_counts.get("ready", 0)
    pending_count = status_counts.get("pending", 0)
    failed_count = status_counts.get("failed", 0)
    return {
        "canonical_ready": bool(ready_count),
        "ai_groups_ready": bool(ready_count),
        "pending_group_count": pending_count,
        "ready_group_count": ready_count,
        "failed_group_count": failed_count,
        "worker_online": queue_health.worker_online,
        "required_queues_ready": queue_health.required_queues_ready,
        "queue_depth": queue_health.queue_depth,
        "last_ai_error": last_ai_error,
    }


def build_post_login_readiness_response(
    settings: Settings,
    *,
    user,
    dashboard: DashboardResponse | None = None,
    mailbox: MailboxResponse | None = None,
) -> PostLoginReadinessResponse:
    database_url = str(settings.database_path)
    state = get_import_state(database_url, user_id=user.id)
    snapshot = get_app_session_snapshot(database_url, user_id=user.id)
    smart_inbox = SmartInboxResponse.model_validate(snapshot.smart_inbox) if snapshot is not None else None
    recent_since = (datetime.now(timezone.utc) - timedelta(days=_recent_visible_days(settings))).isoformat()
    dashboard_since = (datetime.now(timezone.utc) - timedelta(days=DASHBOARD_DAYS)).isoformat()
    ready_mail_group_count = count_mail_groups(database_url, user_id=user.id)
    recent_ready_count = count_ready_mail_groups_since(
        database_url,
        user_id=user.id,
        since_iso=recent_since,
        mailbox_label="inbox",
    )
    recent_status_counts = count_mail_groups_by_enrichment_status_since(
        database_url,
        user_id=user.id,
        since_iso=recent_since,
        mailbox_label="inbox",
    )
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
    full_backfill_completed_at = getattr(state, "full_backfill_completed_at", None) if state is not None else None
    full_import_running = bool(state and state.first_batch_imported_at and not full_backfill_completed_at and (active_backfill_jobs or state.full_backfill_cursor))
    hot_window_ready = _smart_inbox_product_ready(state=state, smart_inbox=smart_inbox)
    if not hot_window_ready:
        hot_window_ready = hot_window_product_ready(
            settings,
            user_id=user.id,
            state=state,
            recent_since=recent_since,
            status_counts=recent_status_counts,
            mailbox=mailbox,
        )
    full_import_completed = bool(full_backfill_completed_at)
    has_prior_product = bool(((state and state.first_groups_ready_at) or recent_ready_count) and hot_window_ready)
    mode = "returning" if has_prior_product else "first_time"
    mailbox_ready = mailbox.total_threads > 0 if mailbox is not None else recent_ready_count > 0
    dashboard_ready = bool(ready_dashboard_count > 0 and ((state and state.first_dashboard_ready_at) or ready_mail_group_count > 0))
    if mode == "returning":
        ready_to_enter = mailbox_ready
    else:
        ready_to_enter = bool(
            state
            and state.first_batch_imported_at
            and state.first_groups_ready_at
            and hot_window_ready
            and recent_ready_count > 0
        )
    stage = _post_login_stage(
        mode=mode,
        ready_to_enter=ready_to_enter,
        state=state,
        active_setup_jobs=active_setup_jobs,
        mailbox_ready=mailbox_ready,
        dashboard_ready=dashboard_ready,
        hot_window_ready=hot_window_ready,
    )
    display_name = _first_name(user.profile) or user.profile.display_name or user.email
    return PostLoginReadinessResponse(
        mode=mode,  # type: ignore[arg-type]
        stage=stage,  # type: ignore[arg-type]
        ready_to_enter=ready_to_enter,
        dashboard_ready=dashboard_ready,
        mailbox_ready=mailbox_ready,
        ready_dashboard_count=ready_dashboard_count,
        ready_mail_group_count=recent_ready_count,
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
    hot_window_ready: bool,
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
    if not hot_window_ready:
        return "preparing_inbox"
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
            ai_summary=_stored_mail_group_summary(enrichment),
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
            ai_summary=_stored_mail_group_summary(enrichment),
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
    database_url = str(settings.database_path)
    if not user_can_write_gmail(database_url, user_id=user_id):
        return 0
    groups = list_pending_mail_groups(database_url, user_id=user_id, limit=limit, preferred_group_ids=preferred_group_ids)
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
        ai_error = str(enrichment.get("_ai_error"))[:1000] if enrichment.get("_ai_error") else None
        upsert_mail_group(
            database_url,
            user_id=user_id,
            group_key=group.group_key,
            group_type=enrichment["group_type"],
            ai_title=enrichment["ai_title"],
            ai_summary=_stored_mail_group_summary(enrichment),
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
            enrichment_status="ready" if ai_ready else "failed" if ai_error else "pending",
            membership_source=group.membership_source,
            ai_model=settings.openai_model if ai_ready else None,
            ai_error=ai_error,
            ai_generated_at=datetime.now(timezone.utc).isoformat() if ai_ready else None,
            **_classification_kwargs(enrichment, generated_at=datetime.now(timezone.utc).isoformat() if ai_ready else None),
        )
        replace_group_members(
            database_url,
            user_id=user_id,
            group_id=group.id,
            members=[(message, group.membership_source, 0.9) for message in messages],
        )
        if ai_ready:
            _persist_message_ai_titles(
                database_url,
                user_id=user_id,
                enrichment=enrichment,
                messages=messages,
                generated_at=datetime.now(timezone.utc).isoformat(),
            )
        touched += 1
    return touched


def run_first_run_ai_grouping(settings: Settings, *, user_id: str, limit: int = FIRST_BATCH_SIZE) -> int:
    database_url = str(settings.database_path)
    if not user_can_write_gmail(database_url, user_id=user_id):
        mark_import_error(database_url, user_id=user_id, error="Gmail is disconnected or data deletion is active.")
        raise RuntimeError("Gmail is disconnected or data deletion is active.")
    state = get_import_state(database_url, user_id=user_id)
    hot_window_completed = bool(
        state
        and (
            getattr(state, "hot_window_completed_at", None)
            or getattr(state, "full_backfill_completed_at", None)
        )
    )
    if not hot_window_completed:
        ensure_background_import_work(settings, user_id=user_id)
        return 0
    recent_since = (datetime.now(timezone.utc) - timedelta(days=_recent_visible_days(settings))).isoformat()
    all_messages = list_recent_messages_since(
        database_url,
        user_id=user_id,
        since_iso=recent_since,
        limit=None,
    )
    if not all_messages:
        all_messages = list_recent_messages(database_url, user_id=user_id, limit=None)
    candidate_batches = _first_run_candidate_message_batches(all_messages)
    if not candidate_batches:
        mark_import_error(database_url, user_id=user_id, error="No Gmail messages were imported for first-run grouping.")
        raise RuntimeError("No Gmail messages were imported for first-run grouping.")
    try:
        created = 0
        visible_created = 0
        used_message_ids: set[str] = set()
        for candidate_messages in candidate_batches:
            grouped_outputs = _ai_batch_group_messages(settings, messages=candidate_messages)
            batch_created, batch_visible_created, batch_used_message_ids = _store_ai_batch_groups(
                settings,
                user_id=user_id,
                messages=candidate_messages,
                grouped_outputs=grouped_outputs,
            )
            created += batch_created
            visible_created += batch_visible_created
            used_message_ids.update(batch_used_message_ids)
        if created < 3 and visible_created < 1:
            raise RuntimeError("AI grouping returned insufficient product-quality groups.")
        ungrouped_message_ids = [
            message.message_id
            for message in all_messages
            if message.message_id not in used_message_ids and _message_matches_mailbox_label(message, "inbox")
        ]
        if ungrouped_message_ids:
            rebuild_touched_mail_groups(settings, user_id=user_id, message_ids=ungrouped_message_ids, use_ai=False)
            _enqueue_hot_window_mail_group_enrich(settings, user_id=user_id)
        mark_import_completed(database_url, user_id=user_id, groups_ready=True, dashboard_ready=False)
        refresh_visible_mail_projection(settings, user_id=user_id, include_ai_lifecycle=False)
        refresh_app_session_snapshot(settings, user_id=user_id, include_dashboard=False)
        enqueue_app_session_snapshot_refresh(settings, user_id=user_id, priority=8)
        return created
    except Exception as exc:
        mark_import_error(database_url, user_id=user_id, error=f"first_run_ai_grouping failed: {exc}")
        raise


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
    groups = [group for group in groups if _group_has_dashboard_visible_mail(group_messages.get(group.id, []))]
    groups = _dedupe_groups_by_messages(groups, group_messages)
    groups = _collapse_dashboard_workflow_groups(groups, group_messages)
    status_counts = count_mail_groups_by_enrichment_status(database_url, user_id=user_id)
    last_ai_error = latest_mail_group_ai_error(database_url, user_id=user_id)
    feed = FeedResponse()
    for group in groups:
        _append_feed_item(feed, _attention_item_from_group(group))
    for task in manual_tasks:
        _append_feed_item(feed, _attention_item_from_manual_task(task))
    briefing = _dashboard_briefing(profile=profile, feed=feed)
    runtime_status = {
        "feed_source": "ai_mail_groups" if groups else "manual_tasks" if manual_tasks else "empty",
        "last_mailbox_sync_at": None,
        "queue_lag_seconds": None,
        "stale_reason": None,
        **_mail_runtime_status(database_url, status_counts=status_counts, last_ai_error=last_ai_error),
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
    return DashboardResponse(
        auth=auth,
        profile=profile,
        briefing=briefing,
        feed=feed,
        runtime_status=runtime_status,
    )


def _first_run_candidate_messages(messages: list[GmailMessageRecord]) -> list[GmailMessageRecord]:
    batches = _first_run_candidate_message_batches(messages)
    return batches[0] if batches else []


def _first_run_candidate_message_batches(messages: list[GmailMessageRecord]) -> list[list[GmailMessageRecord]]:
    limit = max(1, FIRST_RUN_AI_CANDIDATE_LIMIT)
    eligible_messages = [message for message in messages if _first_run_ai_candidate_is_visible(message)]
    ranked = sorted(eligible_messages, key=_first_run_message_score, reverse=True)
    selected_ids: set[str] = set()
    batches: list[list[GmailMessageRecord]] = []
    current_batch: list[GmailMessageRecord] = []

    def flush_current_batch() -> None:
        nonlocal current_batch
        if current_batch:
            batches.append(current_batch)
            current_batch = []

    def append_batch_item(message: GmailMessageRecord) -> None:
        nonlocal current_batch
        if message.message_id in selected_ids:
            return
        if len(current_batch) >= limit:
            flush_current_batch()
        current_batch.append(message)
        selected_ids.add(message.message_id)

    for cluster in _first_run_exact_reference_clusters(ranked):
        cluster_messages = [message for message in cluster if message.message_id not in selected_ids]
        if not cluster_messages:
            continue
        if current_batch and len(current_batch) + len(cluster_messages) > limit:
            flush_current_batch()
        current_batch.extend(cluster_messages)
        selected_ids.update(message.message_id for message in cluster_messages)
        if len(current_batch) >= limit:
            flush_current_batch()
    for message in ranked:
        append_batch_item(message)
    flush_current_batch()
    return batches


def _first_run_ai_candidate_is_visible(message: GmailMessageRecord) -> bool:
    labels = {label.upper() for label in message.label_ids}
    if labels & {"TRASH", "SPAM", "DRAFT"}:
        return False
    return bool(labels & {"INBOX", "SENT"})


def _first_run_exact_reference_cluster_messages(messages: list[GmailMessageRecord]) -> list[GmailMessageRecord]:
    cluster_messages: list[GmailMessageRecord] = []
    for cluster in _first_run_exact_reference_clusters(messages):
        cluster_messages.extend(cluster)
    return cluster_messages


def _first_run_exact_reference_clusters(messages: list[GmailMessageRecord]) -> list[list[GmailMessageRecord]]:
    clusters: dict[str, list[GmailMessageRecord]] = defaultdict(list)
    for message in messages:
        cluster_key = _first_run_reference_cluster_key(message)
        if cluster_key:
            clusters[cluster_key].append(message)
    ranked_clusters: list[tuple[tuple[int, str], str, list[GmailMessageRecord]]] = []
    for cluster_key, cluster_messages in clusters.items():
        thread_ids = {
            message.gmail_thread_id or message.message_id
            for message in cluster_messages
            if message.gmail_thread_id or message.message_id
        }
        if len(cluster_messages) < 2 or len(thread_ids) < 2:
            continue
        ordered = sorted(cluster_messages, key=_first_run_message_score, reverse=True)
        ranked_clusters.append((max(_first_run_message_score(message) for message in ordered), cluster_key, ordered))
    ordered_clusters: list[list[GmailMessageRecord]] = []
    for _score, _cluster_key, ordered in sorted(ranked_clusters, reverse=True):
        ordered_clusters.append(ordered)
    return ordered_clusters


def _first_run_reference_cluster_key(message: GmailMessageRecord) -> str | None:
    signals = message.extracted_signals if isinstance(message.extracted_signals, dict) else {}
    domain = str(signals.get("sender_domain") or sender_domain(message.sender))
    signal_namespace = _signal_group_namespace(message, domain=domain)
    for signal_name in MAILBOX_TASK_REFERENCE_SIGNALS:
        value = signals.get(signal_name)
        if isinstance(value, str) and value:
            namespace = _reference_signal_namespace(signal_name, signal_namespace)
            return f"{signal_name}:{namespace}:{value}"
    return None


def _recent_visible_days(settings: Settings) -> int:
    configured = int(getattr(settings, "gmail_recent_days", RECENT_VISIBLE_DAYS) or RECENT_VISIBLE_DAYS)
    return max(1, min(configured, SMART_HOT_WINDOW_DAYS))


def _first_run_message_score(message: GmailMessageRecord) -> tuple[int, str]:
    labels = {label.upper() for label in message.label_ids}
    signals = message.extracted_signals
    facts, classification = deterministic_classification([message])
    policy = apply_attention_policy(classification, facts)
    score = policy.priority
    if "UNREAD" in labels:
        score += 10
    if "INBOX" in labels:
        score += 5
    for signal_name in MAILBOX_TASK_REFERENCE_SIGNALS:
        if signals.get(signal_name):
            score += 12
            break
    if signals.get("list_id"):
        score -= 15
    if "CATEGORY_PROMOTIONS" in labels or "CATEGORY_SOCIAL" in labels:
        score -= 20
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
    ensure_background_import_work(settings, user_id=user_id)
    state = get_import_state(database_url, user_id=user_id)
    active_backfill_jobs = count_active_jobs(database_url, user_id=user_id, kinds=["gmail_backfill"])
    full_backfill_completed_at = getattr(state, "full_backfill_completed_at", None) if state is not None else None
    full_import_running = bool(state and state.first_batch_imported_at and not full_backfill_completed_at and (state.full_backfill_cursor or active_backfill_jobs))
    status_counts = count_mail_groups_by_enrichment_status(database_url, user_id=user_id)
    mailbox_label = _mailbox_label(label)
    page = list_mailbox_thread_page(
        database_url,
        user_id=user_id,
        label=mailbox_label,
        limit=limit,
        cursor=cursor,
        since_iso=None,
    )
    threads = page.threads
    thread_ai_groups = list_mail_groups_for_gmail_threads(
        database_url,
        user_id=user_id,
        gmail_thread_ids=[thread_id for thread_id, _ in threads],
    )
    thread_ai_groups = _mailbox_compatible_ai_groups(threads, thread_ai_groups)
    render_ai_group_ids = list(
        dict.fromkeys(
            group.id
            for group in thread_ai_groups.values()
            if _mailbox_should_render_ai_group_as_group(group)
        )
    )
    render_ai_group_messages = (
        list_messages_for_groups(database_url, user_id=user_id, group_ids=render_ai_group_ids)
        if render_ai_group_ids
        else {}
    )
    projected_groups = (
        list_visible_groups_for_gmail_threads(
            database_url,
            user_id=user_id,
            visibility="inbox",
            gmail_thread_ids=[thread_id for thread_id, _ in threads],
        )
        if mailbox_label in {"inbox", "all"}
        else {}
    )
    projected_group_ids = list(dict.fromkeys(group.id for group in projected_groups.values()))
    projected_group_messages = list_messages_for_visible_groups(database_url, user_id=user_id, visible_group_ids=projected_group_ids)
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
        ai_group = thread_ai_groups.get(thread_id)
        if ai_group is not None and _mailbox_should_render_ai_group_as_group(ai_group):
            grouped_messages = render_ai_group_messages.get(ai_group.id, [])
            grouped_thread_ids = {
                message.gmail_thread_id or message.message_id
                for message in grouped_messages
                if message.gmail_thread_id or message.message_id
            }
            if len(grouped_thread_ids) > 1 and _ai_group_has_mailbox_messages(grouped_messages, mailbox_label):
                rendered_thread_ids.update(grouped_thread_ids)
                rows.append(_gmail_row_from_ai_group(ai_group, grouped_messages, mailbox_label))
                continue
        rendered_thread_ids.add(thread_id)
        row = _gmail_row_from_canonical_thread(thread_id, messages, mailbox_label, ai_group)
        entries.append(
            MailboxDisplayClusterEntry(
                thread_id=thread_id,
                messages=messages,
                row=row,
                cluster_key=_mailbox_display_cluster_key(thread_id=thread_id, messages=messages, mailbox_label=mailbox_label),
            )
        )
    rows.extend(_mailbox_display_cluster_rows(entries, mailbox_label=mailbox_label))
    rows = _mailbox_rows_absorbing_exact_references(rows, mailbox_label=mailbox_label)
    rows = _dedupe_mailbox_rows_by_source_messages(rows)
    rows = _sort_mailbox_rows(rows)
    _enqueue_visible_enrichment(settings, user_id=user_id, rows=rows)
    return MailboxResponse(
        label=mailbox_label,
        total_threads=count_mailbox_threads(database_url, user_id=user_id, label=mailbox_label, since_iso=None),
        next_cursor=page.next_cursor,
        loaded_threads=page.loaded_threads,
        window_days=None,
        sections=_bucket_rows(rows),
        ready_count=status_counts.get("ready", 0),
        pending_count=status_counts.get("pending", 0),
        mailbox_revision=latest_gmail_mailbox_revision(database_url, user_id=user_id),
        generated_at=datetime.now(timezone.utc).isoformat(),
        oldest_imported_at=oldest_imported_message_at(database_url, user_id=user_id),
        full_import_running=full_import_running,
        full_import_completed=bool(full_backfill_completed_at),
    )


def _mailbox_compatible_ai_groups(
    threads: list[tuple[str, list[GmailMessageRecord]]],
    groups_by_thread_id: dict[str, MailGroupRecord],
) -> dict[str, MailGroupRecord]:
    compatible: dict[str, MailGroupRecord] = {}
    for thread_id, messages in threads:
        group = groups_by_thread_id.get(thread_id)
        if group is None or _mailbox_ai_group_excluded_for_thread(group, thread_id=thread_id, messages=messages):
            continue
        compatible[thread_id] = group
    return compatible


def _mailbox_should_render_ai_group_as_group(group: MailGroupRecord) -> bool:
    if group.membership_source != "ai_batch":
        return False
    if group.enrichment_status != "ready":
        return False
    if group.group_key.startswith("gmail-thread:"):
        return False
    classification = group.classification if isinstance(group.classification, dict) else {}
    contract = classification.get("grouping_contract") if isinstance(classification.get("grouping_contract"), dict) else {}
    if contract and contract.get("should_show_in_inbox") is False:
        return False
    if compact_text(str(contract.get("group_kind") or "")).lower() == "collection":
        return False
    return bool(group.ai_title)


def _dedupe_mailbox_rows_by_source_messages(rows: list[GmailThreadRow]) -> list[GmailThreadRow]:
    candidates: list[tuple[int, GmailThreadRow, set[str]]] = [
        (index, row, _mailbox_row_message_ids(row))
        for index, row in enumerate(rows)
    ]
    kept: list[tuple[int, GmailThreadRow, set[str]]] = []
    for index, row, message_ids in sorted(candidates, key=lambda item: _mailbox_row_dedupe_score(item[1], item[2]), reverse=True):
        if message_ids and any(message_ids <= kept_message_ids for _kept_index, _kept_row, kept_message_ids in kept):
            continue
        kept.append((index, row, message_ids))
    return [row for _index, row, _message_ids in sorted(kept, key=lambda item: item[0])]


def _mailbox_rows_absorbing_exact_references(rows: list[GmailThreadRow], *, mailbox_label: str) -> list[GmailThreadRow]:
    if mailbox_label not in {"inbox", "all"} or len(rows) < 2:
        return rows

    buckets: dict[tuple[str, str], list[GmailThreadRow]] = OrderedDict()
    for row in rows:
        for key in sorted(_mailbox_row_exact_reference_keys(row)):
            buckets.setdefault(key, []).append(row)

    covered_thread_ids: set[str] = set()
    merged_rows: list[GmailThreadRow] = []
    for reference_key, bucket_rows in buckets.items():
        signal_name, reference_value = reference_key
        active_rows = [row for row in bucket_rows if row.thread_id not in covered_thread_ids]
        if len(active_rows) < 2:
            continue
        if not _mailbox_exact_reference_bucket_is_safe(signal_name, reference_value, active_rows):
            continue
        merged_rows.append(_mailbox_row_from_exact_reference_bucket(signal_name, reference_value, active_rows))
        covered_thread_ids.update(row.thread_id for row in active_rows)

    if not merged_rows:
        return rows
    return [*merged_rows, *[row for row in rows if row.thread_id not in covered_thread_ids]]


def _mailbox_rows_absorbing_trade_references(rows: list[GmailThreadRow], *, mailbox_label: str) -> list[GmailThreadRow]:
    return _mailbox_rows_absorbing_exact_references(rows, mailbox_label=mailbox_label)


def _mailbox_row_exact_reference_keys(row: GmailThreadRow) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for signal_name in MAILBOX_EXACT_ABSORPTION_SIGNALS:
        values = _mailbox_row_reference_values(row, signal_name=signal_name)
        if len(values) == 1:
            keys.add((signal_name, next(iter(values))))
    return keys


def _mailbox_exact_reference_bucket_is_safe(signal_name: str, reference_value: str, rows: list[GmailThreadRow]) -> bool:
    if len(rows) < 2:
        return False
    for row in rows:
        if _mailbox_row_reference_values(row, signal_name=signal_name) != {reference_value}:
            return False
    if signal_name == "trade_id":
        return True
    if signal_name in {"ticket_id", "repair_id", "booking_id", "tracking_id", "order_id"}:
        return _mailbox_reference_bucket_provider_matches(rows)
    return False


def _mailbox_row_trade_reference_values(row: GmailThreadRow) -> set[str]:
    return _mailbox_row_reference_values(row, signal_name="trade_id")


def _mailbox_row_reference_values(row: GmailThreadRow, *, signal_name: str) -> set[str]:
    return {
        _mailbox_normalized_visible_reference(item.get("value") or "")
        for item in _smart_mailbox_row_visible_references(row)
        if item.get("signal_name") == signal_name and item.get("value")
    }


def _mailbox_reference_bucket_provider_matches(rows: list[GmailThreadRow]) -> bool:
    token_sets = [_mailbox_row_provider_tokens(row) for row in rows]
    if any(not tokens for tokens in token_sets):
        return False
    shared_tokens = set(token_sets[0])
    for tokens in token_sets[1:]:
        shared_tokens &= tokens
    return bool(shared_tokens)


def _mailbox_row_provider_tokens(row: GmailThreadRow) -> set[str]:
    texts: list[str] = []
    metadata = row.grouping_metadata if isinstance(row.grouping_metadata, dict) else {}
    reference = metadata.get("reference") if isinstance(metadata.get("reference"), dict) else {}
    provider = reference.get("provider") if isinstance(reference, dict) else None
    if isinstance(provider, str):
        texts.append(provider)
    texts.extend(value for value in [row.sender, row.latest_sender, *row.participants] if value)

    stop_words = (
        SMART_WORKFLOW_TOKEN_STOP_WORDS
        | SERVICE_CHANNEL_WORDS
        | GENERIC_SENDER_SLUGS
        | {"bank", "co", "com", "email", "in", "india", "limited", "ltd", "mail", "net", "org", "pvt", "team", "via"}
    )
    tokens: set[str] = set()
    for text in texts:
        name, address = parseaddr(text or "")
        candidates = [text, name]
        if address and (domain_owner := _domain_owner_title_from_address(address)):
            candidates.append(domain_owner)
        for candidate in candidates:
            tokens.update(
                token
                for token in re.findall(r"[a-z0-9]+", compact_text(candidate).lower())
                if len(token) > 1 and token not in stop_words
            )
    return tokens


def _mailbox_row_from_exact_reference_bucket(signal_name: str, reference_value: str, rows: list[GmailThreadRow]) -> GmailThreadRow:
    if signal_name == "trade_id":
        return _mailbox_row_from_trade_reference_bucket(reference_value, rows)
    if signal_name == "ticket_id":
        return _mailbox_row_from_support_reference_bucket(reference_value, rows)
    return _mailbox_row_from_generic_reference_bucket(signal_name, reference_value, rows)


def _mailbox_row_from_trade_reference_bucket(trade_id: str, rows: list[GmailThreadRow]) -> GmailThreadRow:
    ordered_rows = sorted(rows, key=lambda row: row.latest_message_at or row.latest_received_at or "", reverse=True)
    latest = ordered_rows[0]
    children = _mailbox_reference_children(ordered_rows)
    message_count = _mailbox_reference_message_count(ordered_rows, children)
    title = f"FX Retail trade {_mailbox_reference_display_value('trade_id', trade_id)}"
    summary = f"{title} combines the trade confirmation with the related bank/support validation thread."
    cluster_key = f"financial_transfer:ref:fx-retail:trade_id:{trade_id}"
    label_ids = _dedupe_strings([label for row in ordered_rows for label in row.label_ids])
    labels = _dedupe_strings([label for row in ordered_rows for label in row.labels])

    return latest.model_copy(
        update={
            "thread_id": _mailbox_display_cluster_id(cluster_key),
            "entity_id": _mailbox_display_cluster_id(cluster_key),
            "title": title,
            "latest_source_record_id": latest.latest_source_record_id,
            "latest_subject": latest.latest_subject,
            "latest_sender": "FX Retail",
            "sender": "FX Retail",
            "participants": _dedupe_strings([sender for child in children for sender in [child.sender] if sender]),
            "message_count": message_count,
            "summary": summary,
            "ai_group_id": None,
            "ai_title": title,
            "ai_summary": summary,
            "snippet": summary,
            "has_attachments": any(row.has_attachments for row in ordered_rows),
            "attachment_count": sum(row.attachment_count for row in ordered_rows),
            "label_ids": label_ids,
            "labels": labels,
            "unread": any(row.unread for row in ordered_rows),
            "action_needed": any(row.action_needed for row in ordered_rows),
            "action_type": _mailbox_merged_action_type(ordered_rows),
            "action_type_key": _mailbox_merged_action_type(ordered_rows),
            "priority": max((row.priority for row in ordered_rows), default=0),
            "dashboard_visible": any(row.dashboard_visible for row in ordered_rows),
            "current_state": latest.current_state,
            "lifecycle_state": latest.lifecycle_state,
            "outcome_type": latest.outcome_type,
            "lifecycle_updates": [update for row in ordered_rows for update in row.lifecycle_updates],
            "children": children,
            "enrichment_status": "ready",
            "presentation_status": "ai_ready",
            "grouping_metadata": {
                "source": "mailbox_trade_reference_absorption",
                "cluster_key": cluster_key,
                "family": "financial_transfer",
                "reference": {
                    "provider": "fx-retail",
                    "signal_name": "trade_id",
                    "value": trade_id,
                    "message_count": message_count,
                    "direct_message_count": message_count,
                    "direct_message_ids": [child.message_id for child in children][:20],
                },
            },
        }
    )


def _mailbox_row_from_support_reference_bucket(ticket_id: str, rows: list[GmailThreadRow]) -> GmailThreadRow:
    ordered_rows = sorted(rows, key=lambda row: row.latest_message_at or row.latest_received_at or "", reverse=True)
    latest = ordered_rows[0]
    primary = _mailbox_reference_primary_row(ordered_rows)
    children = _mailbox_reference_children(ordered_rows)
    message_count = _mailbox_reference_message_count(ordered_rows, children)
    provider_slug = _mailbox_reference_provider_slug(ordered_rows)
    title = _mailbox_support_reference_title(primary, ticket_id, ordered_rows)
    summary = _mailbox_support_reference_summary(title, children)
    cluster_key = f"support_case:ref:{provider_slug}:ticket_id:{ticket_id}"
    label_ids = _dedupe_strings([label for row in ordered_rows for label in row.label_ids])
    labels = _dedupe_strings([label for row in ordered_rows for label in row.labels])

    return primary.model_copy(
        update={
            "thread_id": _mailbox_display_cluster_id(cluster_key),
            "entity_id": _mailbox_display_cluster_id(cluster_key),
            "title": title,
            "latest_source_record_id": latest.latest_source_record_id,
            "latest_subject": latest.latest_subject,
            "latest_sender": latest.latest_sender,
            "sender": primary.sender or latest.sender,
            "participants": _dedupe_strings([sender for child in children for sender in [child.sender] if sender]),
            "message_count": message_count,
            "summary": summary,
            "ai_group_id": None,
            "ai_title": title,
            "ai_summary": summary,
            "snippet": summary,
            "has_attachments": any(row.has_attachments for row in ordered_rows),
            "attachment_count": sum(row.attachment_count for row in ordered_rows),
            "label_ids": label_ids,
            "labels": labels,
            "unread": any(row.unread for row in ordered_rows),
            "action_needed": any(row.action_needed for row in ordered_rows),
            "action_type": _mailbox_merged_action_type(ordered_rows),
            "action_type_key": _mailbox_merged_action_type(ordered_rows),
            "priority": max((row.priority for row in ordered_rows), default=0),
            "dashboard_visible": any(row.dashboard_visible for row in ordered_rows),
            "current_state": latest.current_state,
            "lifecycle_state": latest.lifecycle_state,
            "outcome_type": latest.outcome_type,
            "lifecycle_updates": [update for row in ordered_rows for update in row.lifecycle_updates],
            "children": children,
            "enrichment_status": "ready",
            "presentation_status": "ai_ready",
            "grouping_metadata": {
                "source": "mailbox_exact_reference_absorption",
                "cluster_key": cluster_key,
                "family": "support_case",
                "reference": {
                    "provider": provider_slug,
                    "signal_name": "ticket_id",
                    "value": ticket_id,
                    "message_count": message_count,
                    "direct_message_count": message_count,
                    "direct_message_ids": [child.message_id for child in children][:20],
                },
            },
        }
    )


def _mailbox_row_from_generic_reference_bucket(signal_name: str, reference_value: str, rows: list[GmailThreadRow]) -> GmailThreadRow:
    ordered_rows = sorted(rows, key=lambda row: row.latest_message_at or row.latest_received_at or "", reverse=True)
    latest = ordered_rows[0]
    primary = _mailbox_reference_primary_row(ordered_rows)
    children = _mailbox_reference_children(ordered_rows)
    message_count = _mailbox_reference_message_count(ordered_rows, children)
    provider_slug = _mailbox_reference_provider_slug(ordered_rows)
    family = MAILBOX_REFERENCE_FAMILY_BY_SIGNAL.get(signal_name, "other")
    title = _mailbox_generic_reference_title(primary, signal_name, reference_value, ordered_rows)
    summary = _mailbox_generic_reference_summary(title, signal_name, children)
    cluster_key = f"{family}:ref:{provider_slug}:{signal_name}:{reference_value}"
    label_ids = _dedupe_strings([label for row in ordered_rows for label in row.label_ids])
    labels = _dedupe_strings([label for row in ordered_rows for label in row.labels])

    return primary.model_copy(
        update={
            "thread_id": _mailbox_display_cluster_id(cluster_key),
            "entity_id": _mailbox_display_cluster_id(cluster_key),
            "title": title,
            "latest_source_record_id": latest.latest_source_record_id,
            "latest_subject": latest.latest_subject,
            "latest_sender": latest.latest_sender,
            "sender": primary.sender or latest.sender,
            "participants": _dedupe_strings([sender for child in children for sender in [child.sender] if sender]),
            "message_count": message_count,
            "summary": summary,
            "ai_group_id": None,
            "ai_title": title,
            "ai_summary": summary,
            "snippet": summary,
            "has_attachments": any(row.has_attachments for row in ordered_rows),
            "attachment_count": sum(row.attachment_count for row in ordered_rows),
            "label_ids": label_ids,
            "labels": labels,
            "unread": any(row.unread for row in ordered_rows),
            "action_needed": any(row.action_needed for row in ordered_rows),
            "action_type": _mailbox_merged_action_type(ordered_rows),
            "action_type_key": _mailbox_merged_action_type(ordered_rows),
            "priority": max((row.priority for row in ordered_rows), default=0),
            "dashboard_visible": any(row.dashboard_visible for row in ordered_rows),
            "current_state": latest.current_state,
            "lifecycle_state": latest.lifecycle_state,
            "outcome_type": latest.outcome_type,
            "lifecycle_updates": [update for row in ordered_rows for update in row.lifecycle_updates],
            "children": children,
            "enrichment_status": "ready",
            "presentation_status": "ai_ready",
            "grouping_metadata": {
                "source": "mailbox_exact_reference_absorption",
                "cluster_key": cluster_key,
                "family": family,
                "reference": {
                    "provider": provider_slug,
                    "signal_name": signal_name,
                    "value": reference_value,
                    "message_count": message_count,
                    "direct_message_count": message_count,
                    "direct_message_ids": [child.message_id for child in children][:20],
                },
            },
        }
    )


def _mailbox_generic_reference_title(
    row: GmailThreadRow,
    signal_name: str,
    reference_value: str,
    rows: list[GmailThreadRow],
) -> str:
    sender = _mailbox_reference_best_sender_title(rows) or _mailbox_reference_sender_title(row) or "Provider"
    label = {
        "booking_id": "booking",
        "repair_id": "repair",
        "tracking_id": "tracking",
        "order_id": "order",
    }.get(signal_name, "reference")
    return f"{sender} {label} {_mailbox_reference_display_value(signal_name, reference_value)}"


def _mailbox_reference_best_sender_title(rows: list[GmailThreadRow]) -> str | None:
    candidates = _dedupe_strings(
        candidate
        for row in rows
        for candidate in [_mailbox_reference_sender_title(row)]
        if candidate
    )
    if not candidates:
        return None
    return max(candidates, key=lambda value: (len(re.findall(r"[A-Za-z0-9]+", value)), len(value), value))


def _mailbox_generic_reference_summary(title: str, signal_name: str, children: list[GmailThreadChildRow]) -> str:
    label = {
        "booking_id": "booking",
        "repair_id": "repair",
        "tracking_id": "tracking reference",
        "order_id": "order",
    }.get(signal_name, "reference")
    count = len(children)
    if count <= 1:
        return title
    return f"{title} combines {count} related messages for the same {label}."


def _mailbox_reference_primary_row(rows: list[GmailThreadRow]) -> GmailThreadRow:
    return max(
        rows,
        key=lambda row: (
            row.message_count,
            1 if row.presentation_status == "ai_ready" else 0,
            row.priority,
            row.latest_message_at or row.latest_received_at or "",
        ),
    )


def _mailbox_support_reference_title(row: GmailThreadRow, ticket_id: str, rows: list[GmailThreadRow]) -> str:
    display_value = _mailbox_reference_display_value("ticket_id", ticket_id)
    candidates = _mailbox_support_reference_title_candidates(row, rows)
    reference_titles: list[str] = []
    outcome_titles: list[str] = []
    for title in candidates:
        if not _mailbox_support_reference_title_is_usable(title):
            continue
        compacted = _mailbox_compact_support_reference_title(title, display_value)
        if display_value in compacted or ticket_id in _mailbox_normalized_visible_reference(compacted):
            if not _mailbox_support_reference_title_is_generic(compacted):
                return compacted
            reference_titles.append(compacted)
        if outcome_title := _mailbox_support_reference_outcome_title(title, display_value):
            outcome_titles.append(outcome_title)
    if outcome_titles:
        return max(
            _dedupe_strings(outcome_titles),
            key=lambda title: _mailbox_support_reference_title_score(title, display_value),
        )
    if reference_titles:
        return max(
            _dedupe_strings(reference_titles),
            key=lambda title: _mailbox_support_reference_title_score(title, display_value),
        )
    for title in candidates:
        if _mailbox_support_reference_title_is_usable(title):
            return title
    sender = _mailbox_reference_sender_title(row) or "Provider"
    return f"{sender} service request {display_value}"


def _mailbox_support_reference_title_candidates(primary: GmailThreadRow, rows: list[GmailThreadRow]) -> list[str]:
    ordered_rows = [primary, *[row for row in rows if row.thread_id != primary.thread_id]]
    values: list[str | None] = []
    for row in ordered_rows:
        values.extend([row.ai_title, row.title])
        values.extend(child.ai_title for child in row.children)
        values.append(row.latest_subject)
    return _dedupe_strings([compact_text(value or "") for value in values if compact_text(value or "")])


def _mailbox_support_reference_title_is_usable(title: str) -> bool:
    if not title or len(title) > 72:
        return False
    lowered = title.lower().strip()
    if lowered.startswith(("attention required", "final reminder", "follow-up", "re:", "fwd:")):
        return False
    if "new correspondence added" in lowered:
        return False
    return True


def _mailbox_compact_support_reference_title(title: str, display_value: str) -> str:
    escaped = re.escape(display_value)
    match = re.match(
        rf"(?i)^(?P<provider>[A-Z][A-Za-z0-9& .-]{{1,32}}?)\s+"
        rf"(?:updates?|opens?|opened|says|asks|requests|shares|reports)\s+"
        rf"(?P<topic>.+?)\s+(?:support\s+case|service\s+request|case)\s+{escaped}\b",
        title,
    )
    if match:
        provider = compact_text(match.group("provider")).strip(" :-")
        topic = compact_text(match.group("topic")).strip(" :-")
        if provider and topic:
            compacted = f"{provider} {topic} case {display_value}"
            if len(compacted) <= 72:
                return compacted
    match = re.match(
        r"(?i)^(?P<provider>[A-Z][A-Za-z0-9& .-]{1,32}?)\s+"
        r"(?:updates?|opens?|opened|resolves?|resolved|tracks?)?\s*"
        r"(?:the\s+)?support\s+case\s+for\s+(?P<topic>.+?)\s*$",
        title,
    )
    if match:
        provider = compact_text(match.group("provider")).strip(" :-")
        topic = re.sub(r"(?i)\b(?:issue|problem|request|ticket)\b", " ", compact_text(match.group("topic")))
        topic = re.sub(r"\s+", " ", topic).strip(" :-")
        if provider and topic:
            compacted = f"{provider} {topic} case {display_value}"
            if len(compacted) <= 72:
                return compacted
    return title


def _mailbox_support_reference_outcome_title(title: str, display_value: str) -> str | None:
    cleaned = compact_text(title)
    match = re.match(
        r"(?i)^(?P<provider>HDFC(?:\s+Bank)?)\s+(?:confirms\s+)?(?:outward\s+)?remittance\s+processed\b",
        cleaned,
    )
    if match:
        provider = "HDFC"
        return f"{provider}: remittance processed for case {display_value}"
    return None


def _mailbox_support_reference_title_score(title: str, display_value: str) -> tuple[int, int, int, str]:
    raw_tokens = re.findall(r"[a-z0-9]+", compact_text(title).lower())
    display_tokens = set(re.findall(r"[a-z0-9]+", display_value.lower()))
    generic_tokens = {
        "added",
        "case",
        "correspondence",
        "day",
        "feedback",
        "follow",
        "new",
        "on",
        "opened",
        "reply",
        "request",
        "service",
        "support",
        "the",
        "update",
        "updated",
    }
    informative_tokens = [
        token
        for token in raw_tokens
        if token not in generic_tokens and token not in display_tokens and not token.isdigit()
    ]
    lowered = compact_text(title).lower()
    generic_penalty = 0
    if re.search(r"\b(?:reply|follow-up|reminder|feedback)\s+(?:on|for)\s+case\b", lowered):
        generic_penalty += 8
    if "new correspondence" in lowered or "service request" in lowered:
        generic_penalty += 6
    specificity = len(set(informative_tokens)) * 3 - generic_penalty
    reference_bonus = 1 if display_value in title or display_value.lower() in lowered else 0
    length_score = max(0, 72 - len(title))
    return (specificity, reference_bonus, length_score, title)


def _mailbox_support_reference_title_is_generic(title: str) -> bool:
    lowered = compact_text(title).lower()
    if re.search(r"\b(?:reply|follow-up|reminder|feedback)\s+(?:on|for)\s+case\b", lowered):
        return True
    if re.search(r"\b\d+[- ]?day\s+follow-up\s+on\s+case\b", lowered):
        return True
    if "new correspondence" in lowered:
        return True
    if re.search(r"\bregisters?\b.*\bcase\s+\d+\b", lowered):
        return True
    if re.search(r"\bregistered\b.*\bservice request\s+\d+\b", lowered):
        return True
    if re.search(r"\bservice request \d+\s+(?:registered|opened|acknowledged)\b", lowered):
        return True
    return False


def _mailbox_support_reference_summary(title: str, children: list[GmailThreadChildRow]) -> str:
    count = len(children)
    if count <= 1:
        return title
    return f"{title} combines {count} related messages for the same service request."


def _mailbox_reference_sender_title(row: GmailThreadRow) -> str | None:
    for value in [row.sender, row.latest_sender, *row.participants]:
        cleaned = _mailbox_reference_clean_sender(value)
        if cleaned:
            return cleaned
    return None


def _mailbox_reference_clean_sender(value: str | None) -> str | None:
    name, address = parseaddr(value or "")
    display_name = compact_text(name)
    normalized_name = re.sub(r"[^a-z0-9]+", "", display_name.lower())
    if "@" in display_name and address and (domain_owner := _domain_owner_title_from_address(address)):
        return domain_owner
    if display_name and normalized_name not in GENERIC_SENDER_SLUGS:
        return display_name
    if address and (domain_owner := _domain_owner_title_from_address(address)):
        return domain_owner
    return compact_text(value or "") or None


def _mailbox_reference_provider_slug(rows: list[GmailThreadRow]) -> str:
    token_sets = [_mailbox_row_provider_tokens(row) for row in rows]
    shared_tokens = set(token_sets[0]) if token_sets else set()
    for tokens in token_sets[1:]:
        shared_tokens &= tokens
    if shared_tokens:
        return sorted(shared_tokens)[0]
    for row in rows:
        tokens = _mailbox_row_provider_tokens(row)
        if tokens:
            return sorted(tokens)[0]
    return "provider"


def _mailbox_reference_children(rows: list[GmailThreadRow]) -> list[GmailThreadChildRow]:
    children: list[GmailThreadChildRow] = []
    seen: set[str] = set()
    for row in rows:
        row_children = row.children or [_mailbox_child_from_row(row)]
        for child in row_children:
            if child.message_id in seen:
                continue
            seen.add(child.message_id)
            children.append(child)
    return sorted(children, key=lambda child: child.received_at or "", reverse=True)


def _mailbox_reference_message_count(rows: list[GmailThreadRow], children: list[GmailThreadChildRow]) -> int:
    explicit_child_ids = {
        child.message_id
        for row in rows
        for child in row.children
        if child.message_id
    }
    fallback_total = sum(max(row.message_count, 1) for row in rows if not row.children)
    return max(len(explicit_child_ids) + fallback_total, len(children), 1)


def _mailbox_child_from_row(row: GmailThreadRow) -> GmailThreadChildRow:
    return GmailThreadChildRow(
        message_id=row.latest_source_record_id,
        gmail_thread_id=row.thread_id,
        sender=row.latest_sender or row.sender,
        subject=row.latest_subject or row.title,
        ai_title=row.ai_title,
        snippet=row.snippet or row.summary,
        received_at=row.latest_received_at,
        label_ids=row.label_ids,
        labels=row.labels,
        unread=row.unread,
    )


def _mailbox_merged_action_type(rows: list[GmailThreadRow]) -> str:
    for row in sorted(rows, key=lambda item: item.priority, reverse=True):
        if row.action_type != "none":
            return row.action_type
    return "open"


def _mailbox_row_dedupe_score(row: GmailThreadRow, message_ids: set[str]) -> tuple[int, int, int, int, str]:
    thread_ids = {child.gmail_thread_id for child in row.children if child.gmail_thread_id}
    if not thread_ids and row.thread_id:
        thread_ids.add(row.thread_id)
    ai_ready = 1 if row.presentation_status == "ai_ready" else 0
    return (
        len(message_ids),
        len(thread_ids),
        ai_ready,
        row.priority,
        row.latest_message_at or row.latest_received_at or "",
    )


def _mailbox_row_message_ids(row: GmailThreadRow) -> set[str]:
    message_ids = {child.message_id for child in row.children if child.message_id}
    if not message_ids and row.latest_source_record_id:
        message_ids.add(row.latest_source_record_id)
    return message_ids


def _mailbox_ai_group_excluded_for_thread(
    group: MailGroupRecord,
    *,
    thread_id: str,
    messages: list[GmailMessageRecord],
) -> bool:
    if group.group_key == f"gmail-thread:{thread_id}" or group.membership_source != "ai_batch":
        return False
    classification = group.classification if isinstance(group.classification, dict) else {}
    raw_contract = classification.get("grouping_contract")
    contract = raw_contract if isinstance(raw_contract, dict) else {}
    if contract.get("should_show_in_inbox") is False:
        return True
    excluded_ids = {str(value) for value in contract.get("excluded_ids") or [] if value}
    if excluded_ids and any(message.message_id in excluded_ids for message in messages):
        return True
    group_kind = compact_text(str(contract.get("group_kind") or "")).lower()
    return group_kind == "collection"


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
        logger.warning("Failed to enqueue visible mail-group enrichment for user %s: %s", user_id, exc)


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
    status_counts = count_mail_groups_by_enrichment_status(database_url, user_id=user_id)
    last_ai_error = latest_mail_group_ai_error(database_url, user_id=user_id)
    queue_health = get_queue_health(database_url)
    poller_workers = [
        worker
        for worker in queue_health.workers
        if worker.get("fresh") and "gmail_poll" in {str(queue) for queue in worker.get("queues", [])}
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
        or (last_ai_error if status_counts.get("ready", 0) == 0 else None)
    )
    return MailboxSyncStateResponse(
        connected=connected,
        last_history_id=state.last_history_id if state else None,
        last_full_sync_at=state.last_import_completed_at if state else None,
        watch_expiration_at=watch_expiration,
        last_sync_started_at=state.last_import_started_at if state else None,
        last_sync_completed_at=state.last_import_completed_at if state else None,
        watch_status=watch_status,
        last_delta_sync_at=state.last_import_completed_at if state else None,
        last_poll_at=str(poller_workers[0].get("last_seen_at")) if poller_workers else None,
        poller_online=bool(poller_workers),
        mailbox_revision=latest_gmail_mailbox_revision(database_url, user_id=user_id),
        last_sync_error=last_sync_error,
        total_threads=count_mailbox_threads(database_url, user_id=user_id, label="all"),
        full_import_running=bool(state and state.first_batch_imported_at and not getattr(state, "full_backfill_completed_at", None) and (state.full_backfill_cursor or count_active_jobs(database_url, user_id=user_id, kinds=["gmail_backfill"]))),
        full_import_completed=bool(getattr(state, "full_backfill_completed_at", None)) if state else False,
        full_import_completed_at=getattr(state, "full_backfill_completed_at", None) if state else None,
        hot_window_started_at=getattr(state, "hot_window_started_at", None) if state else None,
        hot_window_completed_at=getattr(state, "hot_window_completed_at", None) if state else None,
        pending_action_count=count_pending_thread_actions(database_url, user_id=user_id),
        last_ai_error=last_ai_error,
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
    include_summary: bool = False,
) -> ThreadReaderResponse | None:
    database_url = str(settings.database_path)
    if _is_smart_row_reader_id(group_id):
        return _build_smart_row_detail_response(
            settings,
            user_id=user_id,
            reader_id=group_id,
            limit=limit,
            offset=offset,
            include_summary=include_summary,
        )
    if _is_mailbox_display_cluster_id(group_id):
        return _build_mailbox_display_cluster_detail_response(
            settings,
            user_id=user_id,
            cluster_id=group_id,
            limit=limit,
            offset=offset,
            include_summary=include_summary,
        )
    canonical_messages = list_messages_for_gmail_thread(database_url, user_id=user_id, gmail_thread_id=group_id)
    if canonical_messages:
        ai_group = list_mail_groups_for_gmail_threads(database_url, user_id=user_id, gmail_thread_ids=[group_id]).get(group_id)
        if _rebuild_missing_render_documents(settings, user_id=user_id, messages=canonical_messages):
            canonical_messages = list_messages_for_gmail_thread(database_url, user_id=user_id, gmail_thread_id=group_id) or canonical_messages
        if any(_needs_body_fetch(message) for message in canonical_messages):
            if _fetch_body_for_reader_now(settings, user_id=user_id, gmail_thread_id=group_id):
                canonical_messages = list_messages_for_gmail_thread(database_url, user_id=user_id, gmail_thread_id=group_id) or canonical_messages
            if any(_needs_body_fetch(message) for message in canonical_messages):
                _enqueue_body_fetch_for_gmail_thread(settings, user_id=user_id, gmail_thread_id=group_id, priority=90)
        messages = canonical_messages[offset : offset + limit]
        latest_message = max(canonical_messages, key=lambda item: item.internal_date or item.updated_at)
        title = (ai_group.ai_title if ai_group else None) or latest_message.subject
        return ThreadReaderResponse(
            entity_id=ai_group.id if ai_group else group_id,
            user_id=user_id,
            source="gmail",
            gmail_thread_id=group_id,
            subject=latest_message.subject,
            title=title,
            summary=_reader_summary_for_request(
                settings,
                user_id=user_id,
                title=title,
                messages=canonical_messages,
                existing_summary=ai_group.ai_summary if ai_group else None,
                group=ai_group,
            )
            if include_summary
            else None,
            total_messages=len(canonical_messages),
            limit=limit,
            offset=offset,
            has_more=offset + len(messages) < len(canonical_messages),
            messages=[_thread_message_from_gmail(message) for message in messages],
        )

    detail = get_mail_group_detail(database_url, user_id=user_id, group_id=group_id)
    if detail is None:
        return None
    if _rebuild_missing_render_documents(settings, user_id=user_id, messages=detail.messages):
        detail = get_mail_group_detail(database_url, user_id=user_id, group_id=group_id) or detail
    if any(_needs_body_fetch(message) for message in detail.messages):
        if _fetch_body_for_reader_now(settings, user_id=user_id, group_id=group_id):
            detail = get_mail_group_detail(database_url, user_id=user_id, group_id=group_id) or detail
        if any(_needs_body_fetch(message) for message in detail.messages):
            _enqueue_body_fetch_for_group(settings, user_id=user_id, group_id=group_id, priority=90)
    detail_messages = _expand_group_messages_to_canonical_threads(database_url, user_id=user_id, messages=detail.messages)
    if _rebuild_missing_render_documents(settings, user_id=user_id, messages=detail_messages):
        detail_messages = _expand_group_messages_to_canonical_threads(database_url, user_id=user_id, messages=detail.messages)
    messages = detail_messages[offset : offset + limit]
    title = detail.group.ai_title
    return ThreadReaderResponse(
        entity_id=detail.group.id,
        user_id=user_id,
        source="gmail",
        gmail_thread_id=detail.group.id,
        subject=title,
        title=title,
        summary=_reader_summary_for_request(
            settings,
            user_id=user_id,
            title=title,
            messages=detail_messages,
            existing_summary=detail.group.ai_summary,
            group=detail.group,
        )
        if include_summary
        else None,
        total_messages=len(detail_messages),
        limit=limit,
        offset=offset,
        has_more=offset + len(messages) < len(detail_messages),
        messages=[_thread_message_from_gmail(message) for message in messages],
    )


def _build_smart_row_detail_response(
    settings: Settings,
    *,
    user_id: str,
    reader_id: str,
    limit: int,
    offset: int,
    include_summary: bool,
) -> ThreadReaderResponse | None:
    database_url = str(settings.database_path)
    row_id = _smart_row_reader_public_id(reader_id)
    if not row_id:
        return None
    smart_row = get_smart_inbox_row(database_url, user_id=user_id, row_id=row_id)
    if smart_row is None:
        return None
    detail_messages = _smart_row_detail_messages(database_url, user_id=user_id, smart_row=smart_row)
    if not detail_messages:
        return None
    if _rebuild_missing_render_documents(settings, user_id=user_id, messages=detail_messages):
        detail_messages = _smart_row_detail_messages(database_url, user_id=user_id, smart_row=smart_row) or detail_messages
    if any(_needs_body_fetch(message) for message in detail_messages):
        fetched_now = False
        for thread_id in _smart_row_detail_thread_ids(detail_messages, smart_row.source_thread_ids):
            fetched_now = _fetch_body_for_reader_now(settings, user_id=user_id, gmail_thread_id=thread_id) or fetched_now
        if fetched_now:
            detail_messages = _smart_row_detail_messages(database_url, user_id=user_id, smart_row=smart_row) or detail_messages
        if any(_needs_body_fetch(message) for message in detail_messages):
            for thread_id in _smart_row_detail_thread_ids(detail_messages, smart_row.source_thread_ids):
                _enqueue_body_fetch_for_gmail_thread(settings, user_id=user_id, gmail_thread_id=thread_id, priority=90)
    messages = detail_messages[offset : offset + limit]
    latest_message = max(detail_messages, key=lambda item: item.internal_date or item.updated_at or "")
    return ThreadReaderResponse(
        entity_id=smart_row.public_id,
        user_id=user_id,
        source="gmail",
        gmail_thread_id=reader_id,
        subject=latest_message.subject or smart_row.title,
        title=smart_row.title,
        summary=_reader_summary_for_request(
            settings,
            user_id=user_id,
            title=smart_row.title,
            messages=detail_messages,
            existing_summary=None,
        )
        if include_summary
        else None,
        total_messages=len(detail_messages),
        limit=limit,
        offset=offset,
        has_more=offset + len(messages) < len(detail_messages),
        messages=[_thread_message_from_gmail(message) for message in messages],
    )


def _smart_row_detail_messages(database_url: str, *, user_id: str, smart_row) -> list[GmailMessageRecord]:
    by_id: dict[str, GmailMessageRecord] = {}
    for message in list_messages_by_ids(database_url, user_id=user_id, message_ids=smart_row.source_message_ids):
        by_id[message.message_id] = message
    for thread_id in smart_row.source_thread_ids:
        for message in list_messages_for_gmail_thread(database_url, user_id=user_id, gmail_thread_id=thread_id):
            by_id.setdefault(message.message_id, message)
    seed_messages = list(by_id.values())
    if not seed_messages:
        return []
    return _expand_group_messages_to_canonical_threads(database_url, user_id=user_id, messages=seed_messages)


def _smart_row_detail_thread_ids(messages: list[GmailMessageRecord], source_thread_ids: list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            [
                *[thread_id for thread_id in source_thread_ids if thread_id],
                *[message.gmail_thread_id for message in messages if message.gmail_thread_id],
            ]
        )
    )


def _is_smart_row_reader_id(value: str) -> bool:
    return value.startswith(SMART_ROW_READER_PREFIX)


def _smart_row_reader_public_id(value: str) -> str:
    if not _is_smart_row_reader_id(value):
        return ""
    return value[len(SMART_ROW_READER_PREFIX) :].strip()


def _build_mailbox_display_cluster_detail_response(
    settings: Settings,
    *,
    user_id: str,
    cluster_id: str,
    limit: int,
    offset: int,
    include_summary: bool,
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
        if any(_needs_body_fetch(message) for message in detail_messages):
            for thread_id in sorted(thread_ids):
                _enqueue_body_fetch_for_gmail_thread(settings, user_id=user_id, gmail_thread_id=thread_id, priority=90)
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
            summary=_reader_summary_for_request(
                settings,
                user_id=user_id,
                title=row.title or "Email group",
                messages=detail_messages,
                existing_summary=None,
            )
            if include_summary
            else None,
            total_messages=len(detail_messages),
            limit=limit,
            offset=offset,
            has_more=offset + len(messages) < len(detail_messages),
            messages=[_thread_message_from_gmail(message) for message in messages],
        )
    return None


def _reader_summary_for_request(
    settings: Settings,
    *,
    user_id: str,
    title: str | None,
    messages: list[GmailMessageRecord],
    existing_summary: str | None,
    group: MailGroupRecord | None = None,
) -> str | None:
    cached = compact_text(existing_summary or "")
    if cached:
        return cached
    generated = _generate_requested_reader_summary(settings, title=title, messages=messages)
    if not generated:
        return None
    if group is not None:
        try:
            update_mail_group_ai_summary(
                str(settings.database_path),
                user_id=user_id,
                group_id=group.id,
                ai_summary=generated,
            )
        except Exception as exc:
            logger.warning("Could not persist requested reader summary for group %s: %s", group.id, exc)
    return generated


def _generate_requested_reader_summary(
    settings: Settings,
    *,
    title: str | None,
    messages: list[GmailMessageRecord],
) -> str | None:
    if not getattr(settings, "openai_configured", False) or not getattr(settings, "openai_api_key", None):
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=30.0)
        prompt = {
            "task": "Generate an on-demand reader summary for one Gmail inbox item.",
            "rules": [
                "The inbox title is the primary UI; this summary is extra detail because the user explicitly requested it.",
                "Explain the current state, what changed, important references or dates, and any clear next action.",
                "Do not repeat boilerplate, signatures, disclaimers, or raw email templates.",
                "Do not invent actions, outcomes, dates, payments, deliveries, or promises not present in the messages.",
                "Keep it concise and concrete: one short paragraph, maximum three sentences.",
            ],
            "title": title,
            "messages": [
                {
                    "message_id": message.message_id,
                    "gmail_thread_id": message.gmail_thread_id,
                    "subject": message.subject,
                    "sender": message.sender,
                    "date": message.internal_date,
                    "snippet": message.snippet,
                    "text": clean_ai_text(message.text_body or message.snippet or "", max_chars=1600),
                    "signals": message.extracted_signals,
                }
                for message in messages[:16]
            ],
            "output_schema": {"summary": "one short paragraph"},
        }
        response = client.responses.create(
            model=getattr(settings, "openai_model", "gpt-5.4-mini"),
            input=[
                {"role": "system", "content": "Return strict JSON only. Do not include markdown."},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=True)},
            ],
            text={"format": {"type": "json_object"}},
            max_output_tokens=700,
            store=False,
        )
        parsed = json.loads(getattr(response, "output_text", "") or "{}")
        if not isinstance(parsed, dict):
            return None
        summary = compact_text(str(parsed.get("summary") or ""))
        return summary[:1200] or None
    except Exception as exc:
        logger.warning("Reader summary generation failed: %s", exc)
        return None


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
            "task": "Classify one candidate Gmail workflow and provide concise display copy.",
            "rules": [
                "Use Gmail thread identity as the hard mailbox boundary; do not imply unrelated Gmail threads are one mailbox conversation.",
                "Classify the workflow state using the schema. The backend policy engine will decide final section and ranking.",
                "Do not mark newsletters, event ads, or marketing as action_needed unless the user clearly must act.",
                "Do not invent pay, reply, or confirm actions from boilerplate words.",
                "The Inbox title is the primary UI. A user must understand the current status without opening the email or reading the summary.",
                "Return a one-line Inbox title that names the provider/object plus the latest state, outcome, or arrival/resolution timing.",
                "Use concrete title patterns like 'Apple order out for delivery', 'HDFC wire transfer processed', or 'Bank case 106400420 acknowledged'.",
                "Do not stack states in titles: avoid phrases like 'pending response resolved', 'successful confirmed', 'ready confirmed', or 'needed action required'.",
                "Do not return vague titles such as provider updates, support case, application update, order status, status update, new message, or notification.",
                "Do not end titles with only update, updates, status, notice, notification, or message.",
                "Do not return an email subject with Re/Fwd prefixes.",
                "Return concise human titles for individual emails too; do not just copy raw subjects.",
            ],
            "required_json_fields": [
                "workflow_family",
                "workflow_state",
                "requires_user_action",
                "terminal_state",
                "urgency",
                "due_at",
                "confidence",
                "reason",
                "supersedes",
                "ai_title",
                "labels",
                "message_titles",
            ],
            "allowed_workflow_family": [
                "financial_transfer",
                "support_case",
                "application",
                "billing",
                "account_security",
                "logistics",
                "newsletter",
                "marketing",
                "conversation",
                "other",
            ],
            "allowed_workflow_state": ["needs_user_action", "waiting", "resolved", "informational"],
            "allowed_urgency": ["high", "medium", "low"],
            "messages": [
                {
                    "message_id": message.message_id,
                    "subject": message.subject,
                    "sender": message.sender,
                    "date": message.internal_date,
                    "snippet": message.snippet,
                    "text": (message.text_body or "")[:2500],
                    "signals": message.extracted_signals,
                }
                for message in messages[:12]
            ],
            "message_titles_schema": [{"message_id": "existing gmail message id", "ai_title": "individual email title"}],
        }
        response = client.responses.create(
            model=settings.openai_model,
            input=[
                {"role": "system", "content": "Return strict JSON only. Be concise. Classify facts; do not decide dashboard placement."},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=True)},
            ],
            text={"format": {"type": "json_object"}},
            max_output_tokens=MAIL_GROUP_ENRICH_MAX_OUTPUT_TOKENS,
            store=False,
        )
        text_output = getattr(response, "output_text", "") or ""
        parsed = json.loads(text_output)
        enriched = attention_enrichment_payload(messages=messages, group_key=group_key, ai_output=parsed)
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
            "task": "Classify recent Gmail workflows and provide concise display copy for candidate groups.",
            "rules": [
                "Create product-visible AI enrichment groups and lightweight titles for the individual emails inside them.",
                "Group emails by the real-life thing they represent.",
                "Wrong grouping is worse than no grouping. If the shared real-world object is vague, do not group.",
                "Never merge different Gmail threadIds into one mailbox conversation.",
                "Cross-thread Inbox groups must be lifecycle groups for one exact real-world object, not broad collections.",
                "Provider, sender domain, topic words, workflow family, or same week are weak hints only and are not enough to group.",
                "If you create a broad collection, set group_kind=collection and should_show_in_inbox=false.",
                "The backend policy engine decides final dashboard visibility, timing band, priority, and action type from your structured classification.",
                "When a Gmail thread has multiple messages in the input, keep that thread together for conversation-style groups.",
                "Do not mix different task types just because the sender domain matches; for example, application updates, reconsideration replies, and estimated-cost notices are separate items.",
                "Do not mix unrelated organizations, such as a support case, broker notice, or account access mail from different senders.",
                "Do not mark newsletters, event ads, or marketing as action_needed unless the user clearly must act.",
                "Do not invent pay, reply, or confirm actions from boilerplate words.",
                "The Inbox title is the primary UI. A user must understand the current status without opening the email or reading the summary.",
                "Return one-line Inbox titles that name the real-world object plus its current state, outcome, or arrival/resolution timing.",
                "Use concrete title patterns like 'Apple order out for delivery', 'HDFC wire transfer processed', or 'Bank case 106400420 acknowledged'.",
                "Do not stack states in titles: avoid phrases like 'pending response resolved', 'successful confirmed', 'ready confirmed', or 'needed action required'.",
                "Do not return vague titles such as provider updates, support case, application update, order status, status update, new message, or notification.",
                "Do not end titles with only update, updates, status, notice, notification, or message.",
            "Do not return email subjects with Re/Fwd prefixes.",
            "Also return a concise title for each individual email that appears in a group.",
            "A message can appear in only one output group.",
            "For each group, include concrete strong_evidence and excluded_ids for similar emails that should not be included.",
            f"Return at most {FIRST_RUN_AI_MAX_GROUPS} groups for first paint.",
            "Prioritize unread, actionable, delivery, billing, travel, support, application, and account lifecycle groups.",
            "Omit low-signal marketing, newsletter, and routine notification groups from this first response.",
        ],
        "allowed_workflow_family": [
            "financial_transfer",
            "support_case",
            "application",
            "billing",
            "account_security",
            "logistics",
            "newsletter",
            "marketing",
            "conversation",
            "other",
        ],
        "allowed_workflow_state": ["needs_user_action", "waiting", "resolved", "informational"],
        "allowed_urgency": ["high", "medium", "low"],
        "max_groups": FIRST_RUN_AI_MAX_GROUPS,
        "candidate_context": _candidate_context_for_ai(messages),
        "messages": [_message_for_ai(message) for message in messages],
        "output_schema": {
            "groups": [
                {
                    "client_group_key": "stable short key",
                    "group_kind": "conversation | lifecycle | collection | single",
                    "canonical_entity": "normalized sender/org name",
                    "shared_object": "specific real-world case/order/booking/transaction/account event, or empty for collection",
                    "workflow_family": "financial_transfer | support_case | application | billing | account_security | logistics | newsletter | marketing | conversation | other",
                    "workflow_state": "needs_user_action | waiting | resolved | informational",
                    "requires_user_action": False,
                    "terminal_state": False,
                    "urgency": "high | medium | low",
                    "due_at": None,
                    "confidence": 0.0,
                    "risk_level": "high | medium | low",
                    "should_show_in_inbox": False,
                    "should_show_in_dashboard": False,
                    "strong_evidence": ["specific shared IDs, reply-header continuity, or exact lifecycle facts"],
                    "weak_evidence": ["same entity or similar topic hints"],
                    "excluded_ids": ["message ids considered similar but excluded"],
                    "reason": "short classification reason",
                    "supersedes": ["optional message ids this group supersedes"],
                    "ai_title": "specific Inbox title with object/reference and latest state or outcome",
                    "labels": ["short", "labels"],
                    "member_message_ids": ["gmail-message-id"],
                    "message_titles": [{"message_id": "gmail-message-id", "ai_title": "individual email title"}],
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
        max_output_tokens=FIRST_RUN_AI_MAX_OUTPUT_TOKENS,
        store=False,
    )
    parsed = json.loads(getattr(response, "output_text", "") or "{}")
    groups = parsed.get("groups") if isinstance(parsed, dict) else None
    if not isinstance(groups, list):
        raise RuntimeError("AI grouping response did not contain a groups array.")
    return [group for group in groups if isinstance(group, dict)]


def _ai_lifecycle_candidate_messages(messages: list[GmailMessageRecord]) -> list[GmailMessageRecord]:
    candidates: list[GmailMessageRecord] = []
    for message in messages:
        labels = {label.upper() for label in message.label_ids}
        if labels & {"TRASH", "SPAM", "DRAFT"}:
            continue
        if not (labels & {"INBOX", "SENT"}):
            continue
        if "SENT" in labels:
            candidates.append(message)
            continue
        try:
            _facts, classification = deterministic_classification([message])
        except Exception:
            classification = None
        if classification is not None and classification.workflow_family in {"marketing", "newsletter"} and not classification.requires_user_action:
            continue
        candidates.append(message)
    return candidates[:AI_LIFECYCLE_PROJECTION_MESSAGE_LIMIT]


def _ai_lifecycle_group_proposals(settings: Settings, *, messages: list[GmailMessageRecord]) -> list[dict[str, Any]]:
    if not getattr(settings, "openai_configured", False):
        return []
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key, timeout=45.0)
    prompt = {
            "task": "Propose final Inbox lifecycle groups for recent Gmail messages.",
            "rules": [
                "You are the primary judge for whether separate Gmail threads are the same real-world lifecycle.",
                "Use intelligence over regex matching: exact IDs are helpful, but missing IDs do not prevent grouping when the facts clearly refer to one lifecycle.",
                "Wrong merges are worse than missed merges. Only create a normal Inbox group when the shared real-world object is concrete.",
                "Do not create provider/week/topic collections as Inbox groups.",
                "Do not group marketing, newsletters, routine FYI notices, or generic same-provider updates as normal Inbox lifecycle groups.",
                "Sent messages may be context_sent_ids when they explain the user's inquiry, but they must not be the displayed Inbox sender.",
                "member_ids are messages that should belong to the visible lifecycle group. context_sent_ids are supporting sent messages.",
                "Every member_id must have specific per_message_evidence explaining why it belongs.",
                "List excluded_ids for similar nearby messages you considered but rejected.",
                "Use should_show_in_inbox=true only for conversation/lifecycle groups about one concrete object.",
                "The Inbox title is the primary UI. A user must understand the current status without opening the email or reading the summary.",
                "Title formula: provider/object/reference plus the latest state, outcome, or arrival/resolution timing.",
                "Use concrete title patterns like 'Apple order out for delivery', 'HDFC wire transfer processed', or 'Bank case 106400420 acknowledged'.",
                "Do not stack states in titles: avoid phrases like 'pending response resolved', 'successful confirmed', 'ready confirmed', or 'needed action required'.",
                "Do not return titles ending only in update, updates, status, notice, notification, or message.",
                f"Use confidence >= {AI_LIFECYCLE_MIN_INBOX_CONFIDENCE} and risk_level=low only when you would show the group in Inbox.",
                f"Return at most {AI_LIFECYCLE_PROJECTION_MAX_GROUPS} groups.",
        ],
        "allowed_workflow_family": [
            "financial_transfer",
            "support_case",
            "application",
            "billing",
            "account_security",
            "logistics",
            "newsletter",
            "marketing",
            "conversation",
            "other",
        ],
        "candidate_context": _candidate_context_for_ai(messages),
        "messages": [_message_for_ai(message) for message in messages],
        "output_schema": {
            "groups": [
                {
                    "group_kind": "lifecycle | conversation | collection | single",
                    "canonical_entity": "normalized organization/person name",
                    "shared_object": "specific case, ticket, transaction, booking, application, account event, or empty",
                    "workflow_family": "financial_transfer | support_case | application | billing | account_security | logistics | newsletter | marketing | conversation | other",
                    "member_ids": ["visible inbox gmail-message-id"],
                    "context_sent_ids": ["supporting sent gmail-message-id"],
                    "excluded_ids": ["similar message id intentionally excluded"],
                    "confidence": 0.0,
                    "risk_level": "low | medium | high",
                    "per_message_evidence": {"gmail-message-id": "specific factual reason this message is same lifecycle"},
                    "strong_evidence": ["shared lifecycle facts, user inquiry continuity, IDs, reply continuity, or exact outcome facts"],
                    "weak_evidence": ["same entity, same time window, similar language"],
                    "should_show_in_inbox": False,
                    "should_show_in_dashboard": False,
                    "workflow_state": "needs_user_action | waiting | resolved | informational",
                    "requires_user_action": False,
                    "terminal_state": False,
                    "urgency": "high | medium | low",
                    "due_at": None,
                    "reason": "short decision reason",
                    "ai_title": "specific Inbox title naming the lifecycle object/reference and latest state or outcome",
                    "labels": ["short", "labels"],
                    "message_titles": [{"message_id": "gmail-message-id", "ai_title": "specific individual email title"}],
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
                    "Use only message IDs from the input."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=True)},
        ],
        text={"format": {"type": "json_object"}},
        max_output_tokens=AI_LIFECYCLE_PROJECTION_MAX_OUTPUT_TOKENS,
        store=False,
    )
    parsed = json.loads(getattr(response, "output_text", "") or "{}")
    groups = parsed.get("groups") if isinstance(parsed, dict) else None
    if not isinstance(groups, list):
        raise RuntimeError("AI lifecycle response did not contain a groups array.")
    return [group for group in groups if isinstance(group, dict)]


def _store_ai_lifecycle_groups(
    settings: Settings,
    *,
    user_id: str,
    messages: list[GmailMessageRecord],
    grouped_outputs: list[dict[str, Any]],
) -> int:
    database_url = str(settings.database_path)
    by_id = {message.message_id: message for message in messages}
    created = 0
    for index, output in enumerate(grouped_outputs):
        members = _members_for_ai_lifecycle_output(output, by_id)
        visible_members = [message for message in members if "SENT" not in {label.upper() for label in message.label_ids}]
        if len(members) < 2 or not visible_members:
            continue
        if not _ai_lifecycle_output_claims_inbox(output, visible_members):
            continue
        group_key = _ai_lifecycle_group_key(output, members, index)
        enrichment = attention_enrichment_payload(messages=members, group_key=group_key, ai_output=output)
        title = compact_text(str(enrichment.get("ai_title") or ""))
        summary = _stored_mail_group_summary(enrichment, generated_by_ai=True)
        if not title:
            continue
        latest = max(visible_members, key=lambda item: item.internal_date or item.updated_at)
        group_hash = _group_hash(members)
        action_type = _normalize_action_type(enrichment.get("action_type"))
        timing_band = _normalize_timing_band(enrichment.get("timing_band"))
        labels = _normalize_labels(enrichment.get("labels"), members)
        group_type = compact_text(str(enrichment.get("group_type") or output.get("workflow_family") or "other"))[:80] or "other"
        generated_at = datetime.now(timezone.utc).isoformat()
        group = upsert_mail_group(
            database_url,
            user_id=user_id,
            group_key=group_key,
            group_type=group_type,
            ai_title=title[:180],
            ai_summary=summary,
            labels=labels,
            action_needed=bool(enrichment.get("action_needed")),
            action_type=action_type,
            priority=max(0, min(100, _int_or_default(enrichment.get("priority"), 0))),
            timing_band=timing_band,
            dashboard_visible=bool(enrichment.get("dashboard_visible")),
            latest_message_at=latest.internal_date or latest.updated_at,
            latest_message_id=latest.message_id,
            generated_from_hash=group_hash,
            generated_at=generated_at,
            enrichment_status="ready",
            membership_source="ai_lifecycle",
            ai_model=settings.openai_model,
            ai_error=None,
            ai_generated_at=generated_at,
            **_classification_kwargs(enrichment, generated_at=generated_at),
        )
        replace_group_members(
            database_url,
            user_id=user_id,
            group_id=group.id,
            members=[(message, "ai_lifecycle", _ai_lifecycle_confidence(output)) for message in members],
        )
        _persist_message_ai_titles(
            database_url,
            user_id=user_id,
            enrichment={"message_titles": enrichment.get("message_titles")},
            messages=members,
            generated_at=generated_at,
        )
        created += 1
    return created


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
        members = [by_id[message_id] for message_id in member_ids]
        member_thread_ids = {
            message.gmail_thread_id or message.message_id
            for message in members
            if message.gmail_thread_id or message.message_id
        }
        if len(member_thread_ids) > 1 and not _ai_batch_output_claims_inbox_lifecycle(output, members):
            continue
        group_key = _ai_group_key(output.get("client_group_key"), members, index)
        enrichment = attention_enrichment_payload(messages=members, group_key=group_key, ai_output=output)
        title = compact_text(str(enrichment.get("ai_title") or ""))
        summary = _stored_mail_group_summary(enrichment, generated_by_ai=True)
        if not title:
            continue
        used_ids.update(member_ids)
        latest = max(members, key=lambda item: item.internal_date or item.updated_at)
        group_hash = _group_hash(members)
        action_type = _normalize_action_type(enrichment.get("action_type"))
        timing_band = _normalize_timing_band(enrichment.get("timing_band"))
        labels = _normalize_labels(enrichment.get("labels"), members)
        dashboard_visible = bool(enrichment.get("dashboard_visible")) and bool(title)
        group_type = compact_text(str(enrichment.get("group_type") or "other"))[:80] or "other"
        generated_at = datetime.now(timezone.utc).isoformat()
        group = upsert_mail_group(
            database_url,
            user_id=user_id,
            group_key=group_key,
            group_type=group_type,
            ai_title=title[:180],
            ai_summary=summary,
            labels=labels,
            action_needed=bool(enrichment.get("action_needed")),
            action_type=action_type,
            priority=max(0, min(100, _int_or_default(enrichment.get("priority"), 0))),
            timing_band=timing_band,
            dashboard_visible=dashboard_visible,
            latest_message_at=latest.internal_date or latest.updated_at,
            latest_message_id=latest.message_id,
            generated_from_hash=group_hash,
            generated_at=generated_at,
            enrichment_status="ready",
            membership_source="ai_batch",
            ai_model=settings.openai_model,
            ai_error=None,
            ai_generated_at=generated_at,
            **_classification_kwargs(enrichment, generated_at=generated_at),
        )
        replace_group_members(
            database_url,
            user_id=user_id,
            group_id=group.id,
            members=[(message, "ai_batch", 0.95) for message in members],
        )
        _persist_message_ai_titles(
            database_url,
            user_id=user_id,
            enrichment={"message_titles": enrichment.get("message_titles")},
            messages=members,
            generated_at=generated_at,
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
        signal_namespace = _signal_group_namespace(message, domain=domain)
        subject = str(signals.get("normalized_subject") or message.subject or "")[:120]
        if subject:
            context["subject_domain_clusters"][f"{domain}:{subject}"].append(message.message_id)
        for signal_name in MAILBOX_TASK_REFERENCE_SIGNALS:
            value = signals.get(signal_name)
            if isinstance(value, str) and value:
                namespace = _reference_signal_namespace(signal_name, signal_namespace)
                context["strict_signal_clusters"][f"{signal_name}:{namespace}:{value}"].append(message.message_id)
    return {
        key: {cluster_key: ids for cluster_key, ids in value.items() if len(ids) > 1}
        for key, value in context.items()
    }


def _ai_group_key(_client_group_key: Any, members: list[GmailMessageRecord], _index: int) -> str:
    member_hash = hashlib.sha256("\n".join(sorted(message.message_id for message in members)).encode("utf-8")).hexdigest()[:24]
    return f"ai:{member_hash}"


def _members_for_ai_lifecycle_output(output: dict[str, Any], by_id: dict[str, GmailMessageRecord]) -> list[GmailMessageRecord]:
    raw_member_ids = output.get("member_ids") or output.get("member_message_ids") or []
    raw_context_ids = output.get("context_sent_ids") or []
    member_ids: list[str] = []
    for raw_id in [*raw_member_ids, *raw_context_ids] if isinstance(raw_member_ids, list) and isinstance(raw_context_ids, list) else []:
        message_id = str(raw_id)
        if message_id in by_id and message_id not in member_ids:
            member_ids.append(message_id)
    return [by_id[message_id] for message_id in member_ids]


def _ai_batch_output_claims_inbox_lifecycle(output: dict[str, Any], members: list[GmailMessageRecord]) -> bool:
    visible_members = _visible_ai_members(members)
    if len(visible_members) < 2:
        return False
    thread_ids = {
        message.gmail_thread_id or message.message_id
        for message in visible_members
        if message.gmail_thread_id or message.message_id
    }
    if len(thread_ids) < 2:
        return True
    return _ai_lifecycle_output_claims_inbox(output, visible_members)


def _ai_lifecycle_output_claims_inbox(output: dict[str, Any], visible_members: list[GmailMessageRecord]) -> bool:
    if not output.get("should_show_in_inbox"):
        return False
    group_kind = compact_text(str(output.get("group_kind") or "")).lower()
    if group_kind not in {"lifecycle", "conversation"}:
        return False
    workflow_family = compact_text(str(output.get("workflow_family") or "")).lower()
    if workflow_family not in INBOX_STRICT_WORKFLOWS:
        return False
    if compact_text(str(output.get("risk_level") or "")).lower() != "low":
        return False
    if _ai_lifecycle_confidence(output) < AI_LIFECYCLE_MIN_INBOX_CONFIDENCE:
        return False
    if not compact_text(str(output.get("shared_object") or "")):
        return False
    if not _ai_output_has_strong_evidence(output):
        return False
    if not _ai_output_has_per_message_evidence(output, visible_members):
        return False
    if _ai_output_crosses_unrelated_entities(output, visible_members):
        return False
    if _has_conflicting_extracted_references(visible_members):
        return False
    return True


def _visible_ai_members(members: list[GmailMessageRecord]) -> list[GmailMessageRecord]:
    return [message for message in members if "SENT" not in {label.upper() for label in message.label_ids}]


def _ai_output_has_strong_evidence(output: dict[str, Any]) -> bool:
    evidence = output.get("strong_evidence")
    if isinstance(evidence, list):
        return any(compact_text(str(item or "")) for item in evidence)
    return bool(compact_text(str(evidence or "")))


def _ai_output_has_per_message_evidence(output: dict[str, Any], visible_members: list[GmailMessageRecord]) -> bool:
    evidence = output.get("per_message_evidence")
    if isinstance(evidence, dict):
        evidence_ids = {
            str(message_id)
            for message_id, reason in evidence.items()
            if compact_text(str(reason or ""))
        }
    elif isinstance(evidence, list):
        evidence_ids = {
            str(item.get("message_id") or item.get("id"))
            for item in evidence
            if isinstance(item, dict) and compact_text(str(item.get("evidence") or item.get("reason") or ""))
        }
    else:
        evidence_ids = set()
    return bool(visible_members) and {message.message_id for message in visible_members} <= evidence_ids


def _ai_output_crosses_unrelated_entities(output: dict[str, Any], visible_members: list[GmailMessageRecord]) -> bool:
    entity, _channel = canonical_entity_for_messages(visible_members)
    if not _crosses_unrelated_entities(visible_members, entity):
        return False
    provider_roots = _ai_visible_provider_roots(visible_members)
    if len(provider_roots) == 1:
        return False
    claimed_entity = re.sub(r"[^a-z0-9]+", "", compact_text(str(output.get("canonical_entity") or "")).lower())
    if claimed_entity and provider_roots and all(root in claimed_entity or claimed_entity in root for root in provider_roots):
        return False
    return True


def _ai_visible_provider_roots(visible_members: list[GmailMessageRecord]) -> set[str]:
    roots: set[str] = set()
    for message in visible_members:
        signals = message.extracted_signals if isinstance(message.extracted_signals, dict) else {}
        domain = str(signals.get("sender_domain") or sender_domain(message.sender) or "")
        root = re.sub(r"[^a-z0-9]+", "", _domain_slug(domain).lower())
        if root:
            roots.add(root)
    return roots


def _ai_lifecycle_group_key(output: dict[str, Any], members: list[GmailMessageRecord], index: int) -> str:
    canonical = compact_text(str(output.get("canonical_entity") or ""))[:120]
    shared_object = compact_text(str(output.get("shared_object") or ""))[:180]
    source = "\n".join([canonical, shared_object, *sorted(message.message_id for message in members), str(index)])
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]
    return f"ai-lifecycle:{digest}"


def _ai_lifecycle_confidence(output: dict[str, Any]) -> float:
    try:
        return max(0.0, min(1.0, float(output.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        return 0.0


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


def _stored_mail_group_summary(enrichment: dict[str, Any], *, generated_by_ai: bool = False) -> str:
    if (generated_by_ai or enrichment.get("_ai_ready")) and not enrichment.get("_summary_already_cached"):
        return ""
    return compact_text(str(enrichment.get("ai_summary") or ""))[:1200]


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
        "_summary_already_cached": bool(compact_text(group.ai_summary)),
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
        for signal_name in MAILBOX_TASK_REFERENCE_SIGNALS:
            value = signals.get(signal_name)
            if isinstance(value, str) and value:
                namespace = _reference_signal_namespace(signal_name, signal_namespace)
                groups[f"{signal_name}:{namespace}:{value}"].append(message)
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
    """Keep exact-reference groups stable across subdomain variants without provider-specific rules."""
    raw_domains: list[str] = [domain]
    signal_domains = message.extracted_signals.get("domains")
    if isinstance(signal_domains, list):
        raw_domains.extend(str(item) for item in signal_domains if item)
    signal_sender_domain = message.extracted_signals.get("sender_domain")
    if isinstance(signal_sender_domain, str) and signal_sender_domain:
        raw_domains.append(signal_sender_domain)

    domain_slugs = [
        slug
        for raw_domain in raw_domains
        if (slug := _domain_slug(str(raw_domain))) and slug not in GENERIC_SENDER_SLUGS
    ]
    if domain_slugs:
        return sorted(set(domain_slugs), key=lambda item: (len(item), item))[0]

    sender_slug = _sender_slug(message.sender)
    if sender_slug and sender_slug not in GENERIC_SENDER_SLUGS:
        return sender_slug
    return _slug(domain) or "unknown"


def _reference_signal_namespace(signal_name: str, default_namespace: str) -> str:
    if signal_name == "trade_id":
        return "fx-retail"
    return default_namespace


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
    for part in parts:
        if part not in GENERIC_SENDER_SLUGS:
            return _slug(part)
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
    for key in MAILBOX_TASK_REFERENCE_SIGNALS:
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
    matching_messages = [message for message in messages if _message_matches_mailbox_label(message, mailbox_label)]
    row_messages = matching_messages or messages
    latest_message = max(row_messages, key=lambda item: item.internal_date or item.updated_at)
    participants = _mailbox_display_participants(row_messages, mailbox_label)
    display_sender = _mailbox_thread_display_sender(row_messages, latest_message, mailbox_label)
    labels = sorted({label for message in row_messages for label in message.label_ids})
    action_type = "none"
    if ai_group is not None and ai_group.action_type in {"pay", "reply", "confirm", "track", "review", "open", "none"}:
        action_type = _ai_group_mailbox_action_type(ai_group, row_messages)
    presentation_status = _canonical_thread_presentation_status(ai_group, latest_message, display_sender=display_sender)
    title = _canonical_thread_title(latest_message, ai_group, display_sender=display_sender)
    summary = _canonical_thread_summary(latest_message, ai_group, title=title)
    attachment_count = sum(len(gmail_attachments_for_message(message)) for message in row_messages)
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
        message_count=max(1, len(row_messages)),
        summary=summary,
        ai_group_id=ai_group.id if ai_group is not None else None,
        ai_title=title if presentation_status == "ai_ready" else None,
        ai_summary=(ai_group.ai_summary if ai_group is not None else summary) or None,
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
        grouping_metadata=_canonical_thread_grouping_metadata(thread_id, row_messages, ai_group),
    )


def _canonical_thread_grouping_metadata(
    thread_id: str,
    row_messages: list[GmailMessageRecord],
    ai_group: MailGroupRecord | None,
) -> dict[str, Any]:
    metadata = _ai_group_grouping_metadata(ai_group, row_messages) if ai_group is not None else {}
    if len(row_messages) <= 1:
        return metadata
    conversation = {
        "gmail_thread_id": thread_id,
        "message_count": len(row_messages),
        "message_ids": [message.message_id for message in row_messages[:20]],
    }
    return {
        "source": metadata.get("source") or "gmail_thread",
        **metadata,
        "conversation": conversation,
    }


def _mailbox_thread_display_sender(
    row_messages: list[GmailMessageRecord],
    latest_message: GmailMessageRecord,
    mailbox_label: str,
) -> str | None:
    if mailbox_label != "sent" and "SENT" in {label.upper() for label in latest_message.label_ids}:
        inbound_messages = [
            message
            for message in row_messages
            if "SENT" not in {label.upper() for label in message.label_ids}
        ]
        if inbound_messages:
            latest_inbound = max(inbound_messages, key=lambda item: item.internal_date or item.updated_at)
            if sender := _mailbox_display_sender(latest_inbound, mailbox_label):
                return sender
        if recipient := _mailbox_sent_row_recipient_display(latest_message):
            return recipient
    return _mailbox_display_sender(latest_message, mailbox_label)


def _mailbox_sent_row_recipient_display(message: GmailMessageRecord) -> str | None:
    recipient = _first_recipient_display(message)
    if not recipient:
        return None
    name, address = parseaddr(recipient)
    address_value = address if "@" in address else recipient if "@" in recipient else ""
    if address_value:
        domain = address_value.rsplit("@", 1)[1].lower()
        public_mail_domains = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "icloud.com", "me.com", "yahoo.com"}
        domain_owner = "" if domain in public_mail_domains else (_domain_owner_title_from_address(address_value) or "")
        if domain_owner and (" " in domain_owner or domain_owner.isupper()):
            return domain_owner
    return compact_text(name or recipient)


def _canonical_thread_presentation_status(
    ai_group: MailGroupRecord | None,
    latest_message: GmailMessageRecord,
    *,
    display_sender: str | None = None,
) -> str:
    if ai_group is not None:
        return _presentation_status(ai_group, messages=[latest_message], display_sender=display_sender)
    if _canonical_message_ai_title_is_ready(latest_message, latest_message.ai_title, display_sender=display_sender):
        return "ai_ready"
    return "fallback"


def _canonical_thread_title(message: GmailMessageRecord, ai_group: MailGroupRecord | None, *, display_sender: str | None = None) -> str:
    group_title = compact_text(ai_group.ai_title if ai_group is not None else "")
    message_title = _canonical_message_title_candidate(message, display_sender=display_sender)
    if group_title and ai_group is not None and _ai_group_display_title_is_specific(group_title, [message], display_sender=display_sender):
        title = _message_title_replacing_bare_acronym_group_title(group_title, message_title, display_sender) or group_title
        return _normalize_display_title_connector_casing(_polished_canonical_thread_title(message, title, display_sender=display_sender))
    title = message_title or "Untitled email"
    return _normalize_display_title_connector_casing(_polished_canonical_thread_title(message, title, display_sender=display_sender))


def _canonical_message_ai_title_is_ready(message: GmailMessageRecord, title: str | None, *, display_sender: str | None) -> bool:
    raw_title = compact_text(title or "")
    if not raw_title:
        return False
    quality_title = _quality_message_title(message, raw_title)
    return _canonical_display_title_is_specific(message, quality_title, display_sender=display_sender)


def _canonical_message_title_candidate(message: GmailMessageRecord, *, display_sender: str | None) -> str:
    raw_title = compact_text(message.ai_title or "")
    if raw_title:
        quality_title = _quality_message_title(message, raw_title)
        if _canonical_display_title_is_specific(message, quality_title, display_sender=display_sender):
            return quality_title
    fallback = compact_text(message.subject or message.snippet or "")
    if fallback:
        quality_fallback = _quality_message_title(message, fallback)
        return quality_fallback or fallback
    return raw_title


def _canonical_display_title_is_specific(message: GmailMessageRecord, title: str, *, display_sender: str | None) -> bool:
    cleaned = compact_text(title)
    if not cleaned or _display_title_is_vague(cleaned):
        return False
    provider_title = compact_text(display_sender or _summary_provider_name(message))
    return not _mailbox_cluster_topic_ai_title_is_weak(
        cleaned,
        subject=message.subject or "",
        snippet=message.snippet or "",
        provider_title=provider_title,
    )


def _display_title_is_vague(title: str) -> bool:
    normalized = compact_text(title).lower().strip(" -–—:|.,")
    if not normalized:
        return True
    generic_titles = {
        "application update",
        "status update",
        "status updates",
        "case update",
        "case updates",
        "service request",
        "support case",
        "support update",
        "support updates",
        "important update",
        "important updates",
        "order status",
        "order update",
        "order updates",
        "account update",
        "account updates",
        "update",
        "updates",
        "notification",
        "notifications",
        "notice",
        "notices",
        "message",
        "messages",
        "new message",
    }
    if normalized in generic_titles:
        return True
    words = re.findall(r"[a-z0-9]+", normalized)
    if len(words) <= 4 and re.search(
        r"\b(?:updates?|status|notices?|notifications?|messages?|emails?|support case|application update|order status)\b$",
        normalized,
    ):
        return True
    return False


def _polished_canonical_thread_title(message: GmailMessageRecord, title: str, *, display_sender: str | None) -> str:
    cleaned = polish_inbox_title(title)
    if not cleaned:
        return title
    source = compact_text(" ".join(value for value in [message.subject, message.snippet, message.text_body] if value))
    lowered_title = cleaned.lower()
    lowered_source = source.lower()
    provider = compact_text(display_sender or _summary_provider_name(message))

    if "statement" in lowered_title and "your account" in lowered_title:
        suffix = " PDF" if "pdf" in lowered_source else ""
        return f"{provider} account statement{suffix}".strip()

    if "aws cost anomaly detection" in lowered_title and "your account" in lowered_title:
        return "AWS Cost Anomaly Detection enabled"

    if "fx-retail" in lowered_title and "trade summary" in lowered_title:
        date_match = re.search(r"\b(\d{2}-[A-Za-z]{3}-\d{4})\b", source)
        date_clause = f" for {date_match.group(1)}" if date_match else ""
        return f"FX-Retail trade summary{date_clause}"

    if re.search(r"(?i)\b(?:claim|register)\s+(?:for\s+)?your\s+.*ticket\b", cleaned):
        event_match = re.search(r"\b(PyCon\s+DE\s*&\s*PyData\s+20\d{2})\b", source, flags=re.IGNORECASE)
        event = compact_text(event_match.group(1)) if event_match else ""
        if event:
            return f"{event} ticket available"
        return re.sub(r"(?i)\b(?:claim|register)\s+(?:for\s+)?your\s+", "", cleaned).strip() or cleaned

    return cleaned


def _normalize_display_title_connector_casing(title: str) -> str:
    connectors = {"and", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}

    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        if match.start() == 0 or match.end() == len(title):
            return word
        normalized = word.lower()
        return normalized if normalized in connectors and word[:1].isupper() else word

    return re.sub(r"\b[A-Za-z]+\b", replace, title)


def _canonical_thread_summary(message: GmailMessageRecord, ai_group: MailGroupRecord | None, *, title: str) -> str:
    if ai_group is not None and ai_group.ai_summary:
        return compact_text(ai_group.ai_summary)
    return _smart_message_fallback_summary(message, title=title)


def _smart_message_fallback_summary(message: GmailMessageRecord, *, title: str) -> str:
    raw = message.text_body or message.snippet or message.subject or title
    cleaned = _clean_mailbox_fallback_text(raw)
    if not cleaned:
        return title
    structured = _structured_fallback_summary(message, title=title, cleaned=cleaned)
    if structured:
        return structured
    return cleaned[:260].rstrip()


def _clean_mailbox_fallback_text(value: str) -> str:
    decoded = html.unescape(str(value or ""))
    decoded = LOCALIZATION_PLACEHOLDER_RE.sub(" ", decoded)
    decoded = re.sub(r"[\u034f\u061c\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]+", " ", decoded)
    decoded = re.sub(r"\s+", " ", decoded)
    return clean_ai_text(compact_text(decoded))


def _structured_fallback_summary(message: GmailMessageRecord, *, title: str, cleaned: str) -> str | None:
    if not message.ai_title:
        return None
    normalized_title = title.lower()
    normalized_text = cleaned.lower()
    provider = _summary_provider_name(message)
    if "statement" in normalized_title and "pdf" in normalized_text:
        summary = f"{provider} sent an account statement PDF for your records."
        if "password" in normalized_text or "protected" in normalized_text:
            summary += " The statement is password-protected."
        return summary
    if "verif" in normalized_title and "original document" in normalized_text:
        subject = _sentence_case(title)
        return f"{provider} says {subject} and asks you to continue with original documents ready."
    if _looks_like_third_party_access_granted(normalized_title, normalized_text):
        app_name = _third_party_access_app_name(message, cleaned)
        app_clause = f" to {app_name}" if app_name else ""
        return f"{provider} says third-party access was granted{app_clause}. Review it if you did not authorize this connection."
    if _looks_like_account_deletion_warning(normalized_title, normalized_text):
        return f"{provider} says your account is scheduled for deletion after inactivity. Cancel the scheduled deletion if you want to keep the account and its data."
    if _looks_like_support_receipt_acknowledgment(normalized_title, normalized_text):
        topic = _support_receipt_topic(normalized_title, normalized_text)
        timing = _support_receipt_timing(cleaned)
        timing_clause = f" and says someone will respond within {timing}" if timing else ""
        return f"{provider} acknowledged your {topic}{timing_clause}."
    return None


def _looks_like_third_party_access_granted(normalized_title: str, normalized_text: str) -> bool:
    content = f"{normalized_title} {normalized_text}"
    return (
        ("third-party access" in content or "third party application" in content or "third-party application" in content)
        and ("access granted" in content or "granted access" in content)
    )


def _third_party_access_app_name(message: GmailMessageRecord, cleaned: str) -> str:
    subject = compact_text(message.subject or "")
    candidates = [
        re.search(r"(?i)\baccess granted to\s+([a-z0-9][a-z0-9 .&_-]{1,80})", subject),
        re.search(r"(?i)\bapplication:\s*([a-z0-9][a-z0-9 .&_-]{1,80}?)(?:\s+website:|\s+privacy policy:|$)", cleaned),
        re.search(r"(?i)\bthird[- ]party application(?: named)?\s+([a-z0-9][a-z0-9 .&_-]{1,80})", cleaned),
    ]
    for match in candidates:
        if not match:
            continue
        value = _clean_third_party_app_name(match.group(1))
        if value:
            return value
    return ""


def _clean_third_party_app_name(value: str) -> str:
    cleaned = compact_text(re.sub(r"https?://\S+", "", value))
    cleaned = re.sub(r"(?i)\b(?:website|privacy policy|access granted|application)\b.*$", "", cleaned).strip(" .:-")
    return cleaned[:80]


def _looks_like_account_deletion_warning(normalized_title: str, normalized_text: str) -> bool:
    content = f"{normalized_title} {normalized_text}"
    return "account" in content and "scheduled for deletion" in content


def _looks_like_support_receipt_acknowledgment(normalized_title: str, normalized_text: str) -> bool:
    content = f"{normalized_title} {normalized_text}"
    return (
        ("acknowledg" in content or "has been received" in content or "received your" in content)
        and ("complaint" in content or "grievance" in content or "service request" in content or "query" in content or "concern" in content)
        and ("system generated" in content or "auto" in content or "official will respond" in content or "will respond" in content)
    )


def _support_receipt_topic(normalized_title: str, normalized_text: str) -> str:
    content = f"{normalized_title} {normalized_text}"
    if "grievance" in content:
        return "grievance"
    if "complaint" in content or "privacy" in content or "consent" in content:
        return "complaint"
    if "service request" in content:
        return "service request"
    if "query" in content:
        return "query"
    return "message"


def _support_receipt_timing(cleaned: str) -> str | None:
    match = re.search(r"(?i)\b(?:in|within)\s+(\d+\s+(?:working\s+)?days?)\b", cleaned)
    if not match:
        return None
    return match.group(1).lower()


def _summary_provider_name(message: GmailMessageRecord) -> str:
    name, address = parseaddr(message.sender or "")
    display = compact_text(name)
    if display and not _summary_display_name_is_service_channel(display):
        return display
    domain = sender_domain(address or message.sender)
    if domain:
        if domain_title := _domain_owner_title_from_address(f"sender@{domain}"):
            return domain_title
        if domain == "id.me":
            return "ID.me"
        return _domain_slug(domain).replace("-", " ").title()
    return "The sender"


def _summary_display_name_is_service_channel(display: str) -> bool:
    lowered = display.lower()
    tokens = re.findall(r"[a-z0-9]+", lowered)
    if not tokens:
        return False
    if any(marker in lowered for marker in ["grievance", "redressal", "escalation"]):
        return True
    return all(token in SERVICE_CHANNEL_WORDS or token in GENERIC_SENDER_SLUGS for token in tokens)


def _sentence_case(value: str) -> str:
    cleaned = compact_text(value)
    if not cleaned:
        return "the message needs attention"
    return cleaned[:1].lower() + cleaned[1:]


def _gmail_row_from_ai_group(group: MailGroupRecord, messages: list[GmailMessageRecord], mailbox_label: str) -> GmailThreadRow:
    row_messages = sorted(_ai_group_mailbox_messages(messages, mailbox_label), key=lambda item: item.internal_date or item.updated_at or "")
    latest_message = max(row_messages, key=lambda item: item.internal_date or item.updated_at)
    participants = _mailbox_display_participants(row_messages, mailbox_label)
    display_sender = _mailbox_display_sender(latest_message, mailbox_label)
    title = _ai_group_mailbox_title(group, row_messages, display_sender)
    presentation_status = _presentation_status(group, messages=row_messages, display_sender=display_sender)
    labels = sorted({label for message in row_messages for label in message.label_ids})
    attachment_count = sum(len(gmail_attachments_for_message(message)) for message in row_messages)
    action_type = _ai_group_mailbox_action_type(group, row_messages)
    return GmailThreadRow(
        thread_id=group.id,
        entity_id=group.id,
        title=title,
        href=f"/v1/mailbox/threads/{group.id}",
        latest_source_record_id=latest_message.message_id,
        latest_received_at=latest_message.internal_date or latest_message.updated_at,
        latest_message_at=latest_message.internal_date or latest_message.updated_at,
        latest_subject=latest_message.subject,
        latest_sender=latest_message.sender,
        sender=display_sender,
        participants=participants,
        message_count=max(1, len(row_messages)),
        summary=group.ai_summary,
        ai_group_id=group.id,
        ai_title=title if presentation_status == "ai_ready" else None,
        ai_summary=group.ai_summary,
        snippet=latest_message.snippet,
        has_attachments=attachment_count > 0,
        attachment_count=attachment_count,
        label_ids=labels,
        labels=labels,
        unread="UNREAD" in {label.upper() for label in labels},
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
                "subject": message.subject or title,
                "sender": message.sender,
                "summary": message.snippet,
            }
            for message in row_messages[-3:]
        ],
        children=[_gmail_child_row_from_message(message, mailbox_label=mailbox_label) for message in row_messages],
        enrichment_status=group.enrichment_status,
        presentation_status=presentation_status,  # type: ignore[arg-type]
        grouping_metadata=_ai_group_grouping_metadata(group, row_messages),
    )


def _ai_group_grouping_metadata(group: MailGroupRecord, messages: list[GmailMessageRecord]) -> dict[str, Any]:
    classification = group.classification if isinstance(group.classification, dict) else {}
    contract = classification.get("grouping_contract") if isinstance(classification.get("grouping_contract"), dict) else {}
    facts = classification.get("facts") if isinstance(classification.get("facts"), dict) else {}
    reference_id = compact_text(str(classification.get("reference_id") or facts.get("reference_id") or ""))
    evidence = _ai_group_evidence_metadata(contract)
    metadata: dict[str, Any] = {
        "source": "ai_mail_group",
        "group_key": group.group_key,
        "membership_source": group.membership_source,
        "group_type": group.group_type,
    }
    if contract:
        metadata["grouping_contract"] = contract
    if reference_id:
        metadata["reference"] = {"value": reference_id}
    if evidence:
        metadata["evidence"] = evidence
    if contract or evidence or reference_id:
        workflow_family = compact_text(str(classification.get("workflow_family") or group.group_type or ""))
        if workflow_family:
            metadata["workflow"] = {
                "family": workflow_family,
                "state": compact_text(str(classification.get("workflow_state") or "")),
                "requires_user_action": bool(classification.get("requires_user_action")),
            }
    if len(messages) > 1:
        thread_ids = _dedupe_strings([message.gmail_thread_id or message.message_id for message in messages if message.gmail_thread_id or message.message_id])
        metadata["conversation"] = {
            "thread_count": len(thread_ids),
            "message_count": len(messages),
            "thread_ids": thread_ids[:20],
        }
    return metadata


def _ai_group_evidence_metadata(contract: dict[str, Any]) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for key in ["strong_evidence", "weak_evidence", "per_message_evidence", "excluded_ids", "shared_object", "canonical_entity"]:
        value = contract.get(key)
        if _metadata_has_content(value):
            evidence[key] = value
    return evidence


def _metadata_has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_metadata_has_content(item) for item in value.values())
    if isinstance(value, list):
        return any(_metadata_has_content(item) for item in value)
    return bool(value)


def _ai_group_mailbox_title(group: MailGroupRecord, row_messages: list[GmailMessageRecord], display_sender: str | None) -> str:
    group_title = compact_text(group.ai_title)
    latest_message = max(row_messages, key=lambda item: item.internal_date or item.updated_at)
    if group_title and _ai_group_display_title_is_specific(group_title, row_messages, display_sender=display_sender):
        group_title = _polished_canonical_thread_title(latest_message, group_title, display_sender=display_sender)
        if len(row_messages) != 1 or not display_sender:
            return group_title
        message_title = compact_text(row_messages[0].ai_title or "")
        return _message_title_replacing_bare_acronym_group_title(group_title, message_title, display_sender) or group_title
    return _canonical_message_title_candidate(latest_message, display_sender=display_sender) or "Untitled email"


def _message_title_replacing_bare_acronym_group_title(group_title: str, message_title: str, display_sender: str | None) -> str | None:
    if not group_title or not message_title or not display_sender:
        return None
    first_group_word = next(iter(re.findall(r"[A-Za-z0-9]+", group_title)), "")
    if not _compact_acronym_title(first_group_word):
        return None
    if not message_title.lower().startswith(display_sender.lower()):
        return None
    return message_title


def _ai_group_mailbox_action_type(group: MailGroupRecord, messages: list[GmailMessageRecord]) -> str:
    stored = group.action_type if group.action_type in {"pay", "reply", "confirm", "track", "review", "open", "none"} else "open"
    if stored != "reply" or not messages:
        return stored
    try:
        facts, classification = deterministic_classification(messages)
    except Exception:
        return stored
    current = apply_attention_policy(classification, facts).action_type
    return current if current in {"pay", "reply", "confirm", "track", "review", "open", "none"} else stored


def _ai_group_has_mailbox_messages(messages: list[GmailMessageRecord], mailbox_label: str) -> bool:
    return bool(_ai_group_mailbox_messages(messages, mailbox_label))


def _ai_group_mailbox_messages(messages: list[GmailMessageRecord], mailbox_label: str) -> list[GmailMessageRecord]:
    if mailbox_label in {"inbox", "all"}:
        return [
            message
            for message in messages
            if _message_matches_mailbox_label(message, mailbox_label)
            and not ({"SENT", "DRAFT", "TRASH", "SPAM"} & {label.upper() for label in message.label_ids})
        ]
    return [message for message in messages if _message_matches_mailbox_label(message, mailbox_label)]


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
        grouping_metadata=_visible_group_grouping_metadata(group, row_messages),
    )


def _visible_group_grouping_metadata(group: VisibleMailGroupRecord, messages: list[GmailMessageRecord]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source": "visible_mail_projection",
        "projection_source": group.source,
        "group_kind": group.group_kind,
        "workflow_type": group.workflow_type,
        "confidence": group.confidence,
    }
    if group.evidence:
        metadata["evidence"] = group.evidence
        metadata["workflow"] = {
            "family": group.workflow_type,
            "canonical_entity": group.canonical_entity,
            "source_group_id": group.source_group_id,
        }
    if len(messages) > 1:
        metadata["conversation"] = {
            "message_count": len(messages),
            "thread_ids": _dedupe_strings([message.gmail_thread_id or message.message_id for message in messages if message.gmail_thread_id or message.message_id])[:20],
        }
    return metadata


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
    canonical = _humanize_compact_provider_name(compact_text(group.canonical_entity or ""))
    if canonical and not _visible_group_canonical_looks_like_user(canonical, all_messages):
        if domain_owner := _service_channel_canonical_domain_owner(canonical, row_messages):
            if content_title := _provider_title_from_message_titles(row_messages, domain_owner):
                return content_title
            return domain_owner
        if content_title := _provider_title_from_message_titles(row_messages, canonical):
            return content_title
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


def _service_channel_canonical_domain_owner(canonical: str, messages: list[GmailMessageRecord]) -> str | None:
    words: list[str] = []
    for token in re.findall(r"[A-Za-z0-9]+", canonical):
        words.extend(_domain_owner_label_words(token))
    if not words or not all(word in SERVICE_CHANNEL_WORDS for word in words):
        return None
    inbound_messages = [message for message in messages if "SENT" not in {label.upper() for label in message.label_ids}]
    for message in sorted(inbound_messages, key=lambda item: item.internal_date or item.updated_at or "", reverse=True):
        if sender := _mailbox_display_sender(message, "inbox"):
            return sender
    return None


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
    _mailbox_display_add_reference_bridges(entries, buckets)

    clustered_thread_ids: set[str] = set()
    rows: list[GmailThreadRow] = []
    for cluster_key, bucket_entries in buckets.items():
        for row_cluster_key, cluster_entries in _mailbox_display_cluster_entry_slices(cluster_key, bucket_entries):
            thread_ids = {entry.thread_id for entry in cluster_entries}
            if len(thread_ids) < 2:
                continue
            messages = [message for entry in cluster_entries for message in entry.messages]
            if (
                _mailbox_base_cluster_key(row_cluster_key).startswith("newsletter:provider-stream:")
                and _mailbox_provider_stream_has_mixed_operational_topics(messages)
            ):
                continue
            clustered_thread_ids.update(thread_ids)
            rows.append(_gmail_row_from_mailbox_display_cluster(row_cluster_key, messages, mailbox_label=mailbox_label))

    rows.extend(entry.row for entry in entries if entry.thread_id not in clustered_thread_ids)
    return rows


def _mailbox_display_cluster_entry_slices(
    cluster_key: str,
    entries: list[MailboxDisplayClusterEntry],
) -> list[tuple[str, list[MailboxDisplayClusterEntry]]]:
    if not _mailbox_cluster_is_provider_stream(cluster_key):
        return [(cluster_key, entries)]
    sorted_entries = sorted(entries, key=_mailbox_display_cluster_entry_date)
    slices: list[list[MailboxDisplayClusterEntry]] = []
    current: list[MailboxDisplayClusterEntry] = []
    current_start: datetime | None = None
    for entry in sorted_entries:
        entry_date = _mailbox_display_cluster_entry_date(entry)
        if current and current_start is not None and (entry_date - current_start).days > MAILBOX_PROVIDER_STREAM_MAX_SPAN_DAYS:
            slices.append(current)
            current = []
            current_start = None
        if not current:
            current_start = entry_date
        current.append(entry)
    if current:
        slices.append(current)
    if len(slices) <= 1:
        return [(cluster_key, entries)]
    return [(_mailbox_display_temporal_cluster_key(cluster_key, slice_entries), slice_entries) for slice_entries in slices]


def _mailbox_display_cluster_entry_date(entry: MailboxDisplayClusterEntry) -> datetime:
    values = [message.internal_date or message.updated_at for message in entry.messages if message.internal_date or message.updated_at]
    if values:
        return max(_parse_date(value) for value in values)
    return _parse_date(entry.row.latest_message_at or entry.row.latest_received_at or "")


def _mailbox_display_temporal_cluster_key(cluster_key: str, entries: list[MailboxDisplayClusterEntry]) -> str:
    latest = max((_mailbox_display_cluster_entry_date(entry) for entry in entries), default=datetime.now().astimezone())
    return f"{cluster_key}|window:{latest.strftime('%Y%m%d')}"


def _mailbox_display_add_reference_bridges(
    entries: list[MailboxDisplayClusterEntry],
    buckets: dict[str, list[MailboxDisplayClusterEntry]],
) -> None:
    reference_index: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for cluster_key, bucket_entries in buckets.items():
        reference = _mailbox_cluster_reference_parts(cluster_key)
        if reference is None:
            continue
        family = _mailbox_cluster_family(cluster_key)
        if family is None:
            continue
        provider, _signal_name, _reference_value = reference
        for entry in bucket_entries:
            for token in _mailbox_message_reference_tokens(entry.messages):
                reference_index[(family, provider, token)].add(cluster_key)

    if not reference_index:
        return

    bridged_thread_ids: set[str] = set()
    for entry in entries:
        if entry.cluster_key or entry.thread_id in bridged_thread_ids:
            continue
        try:
            facts, classification = deterministic_classification(entry.messages)
        except Exception:
            continue
        if facts.reference_id:
            continue
        family = _mailbox_display_cluster_family(classification.workflow_family, facts.text)
        if family is None or family == "":
            continue
        tokens = _mailbox_message_reference_tokens(entry.messages)
        matching_cluster_keys: set[str] = set()
        for token in tokens:
            matching_cluster_keys.update(reference_index.get((family, facts.provider, token), set()))
        if len(matching_cluster_keys) != 1:
            continue
        cluster_key = next(iter(matching_cluster_keys))
        reference = _mailbox_cluster_reference_parts(cluster_key)
        if reference is not None and not _mailbox_entry_can_bridge_to_reference(entry, reference=reference):
            continue
        buckets[cluster_key].append(entry)
        bridged_thread_ids.add(entry.thread_id)


def _mailbox_entry_can_bridge_to_reference(
    entry: MailboxDisplayClusterEntry,
    *,
    reference: tuple[str, str, str],
) -> bool:
    _provider, signal_name, reference_value = reference
    if any(_mailbox_message_has_direct_reference(message, signal_name=signal_name, reference_value=reference_value) for message in entry.messages):
        return True

    visible_text = _mailbox_visible_reference_text(entry.messages)
    if not visible_text:
        return True

    target = _mailbox_normalized_visible_reference(reference_value)
    visible_values = _mailbox_visible_reference_values(visible_text, signal_name=signal_name)
    if target and target in visible_values:
        return True
    if target and target in _mailbox_normalized_visible_reference(visible_text):
        return True
    return not visible_values


def _mailbox_visible_reference_text(messages: list[GmailMessageRecord]) -> str:
    return " ".join(
        compact_text(value)
        for message in messages
        for value in [message.subject, message.ai_title, message.snippet, message.text_body]
        if compact_text(value or "")
    )


def _mailbox_visible_reference_values(text: str, *, signal_name: str) -> set[str]:
    normalized_text = compact_text(text).upper()
    if signal_name == "ticket_id":
        return {
            _mailbox_normalized_visible_reference(match)
            for match in re.findall(r"\b\d{7,}\b", normalized_text)
        }
    if signal_name == "trade_id":
        return _smart_trade_reference_values_from_text(text)
    return {
        _mailbox_normalized_visible_reference(match)
        for match in re.findall(r"\b[A-Z]{0,8}[-/]?\d[A-Z0-9-]{4,}\b|\b\d{7,}\b", normalized_text)
    }


def _mailbox_normalized_visible_reference(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", compact_text(value).upper())


def _mailbox_message_reference_tokens(messages: list[GmailMessageRecord]) -> set[str]:
    tokens: set[str] = set()
    for message in messages:
        containers = [message.headers, message.extracted_signals if isinstance(message.extracted_signals, dict) else {}]
        for container in containers:
            for key in ["references", "References", "in_reply_to", "In-Reply-To"]:
                raw = container.get(key) if isinstance(container, dict) else None
                if not raw:
                    continue
                value = str(raw)
                ids = re.findall(r"<([^>]+)>", value)
                if ids:
                    tokens.update(ids)
                elif "@" in value:
                    tokens.add(value.strip())
    return {hashlib.sha1(token.encode("utf-8")).hexdigest() for token in tokens if token.strip()}


def _gmail_row_from_mailbox_display_cluster(cluster_key: str, messages: list[GmailMessageRecord], *, mailbox_label: str) -> GmailThreadRow:
    matching_messages = [message for message in messages if _message_matches_mailbox_label(message, mailbox_label)]
    row_messages = sorted(matching_messages or messages, key=lambda item: item.internal_date or item.updated_at or "")
    latest_message = max(row_messages, key=lambda item: item.internal_date or item.updated_at)
    facts, classification = deterministic_classification(row_messages)
    family = _mailbox_cluster_family(cluster_key) or classification.workflow_family
    cluster_id = _mailbox_display_cluster_id(cluster_key)
    labels = sorted({label for message in row_messages for label in message.label_ids})
    attachment_count = sum(len(gmail_attachments_for_message(message)) for message in row_messages)
    provider_title = _mailbox_cluster_provider_title(row_messages, _mailbox_cluster_provider_for_title(cluster_key, facts.provider))
    title = _mailbox_cluster_title(provider_title, family, cluster_key, messages=row_messages)
    summary = _mailbox_cluster_summary(
        provider_title,
        family,
        len({m.gmail_thread_id or m.message_id for m in row_messages}),
        cluster_key=cluster_key,
        messages=row_messages,
    )
    action_type = _cluster_action_type(family)
    priority = _mailbox_cluster_priority(cluster_key, family=family, classification=classification)
    grouping_metadata = _mailbox_cluster_grouping_metadata(cluster_key, family=family, messages=row_messages)
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
        priority=priority,
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
        grouping_metadata=grouping_metadata,
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
    if mailbox_label == "all" and _is_safe_job_alert_display_cluster(messages, classification, facts.text):
        return f"newsletter:provider-stream:{provider}:job-alert"
    if mailbox_label == "all" and _is_safe_provider_stream_display_cluster(messages, classification, facts.text):
        return f"newsletter:provider-stream:{provider}"
    family = _mailbox_display_cluster_family(classification.workflow_family, facts.text)
    reference = facts.reference_id
    if reference:
        family = family or _mailbox_family_from_reference_id(reference)
        if not family:
            return None
        reference_provider = _mailbox_reference_provider(provider, reference=reference, family=family)
        return f"{family}:ref:{reference_provider}:{reference}"
    if not family:
        return None
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
    if _mailbox_provider_stream_has_mixed_operational_topics(messages):
        return False
    is_newsletter = _is_safe_newsletter_display_cluster(messages, classification)
    workflow_family = str(getattr(classification, "workflow_family", "") or "")
    if workflow_family in {"application", "financial_transfer", "support_case", "billing", "logistics"} and not is_newsletter:
        return False
    if is_newsletter:
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
    return any(
        "CATEGORY_UPDATES" in {label.upper() for label in message.label_ids}
        for message in messages
    )


def _mailbox_provider_stream_has_mixed_operational_topics(messages: list[GmailMessageRecord]) -> bool:
    categories = {
        category
        for message in messages
        if (category := _mailbox_provider_stream_operational_topic(message))
    }
    return "developer_automation" in categories and len(categories) > 1


def _mailbox_provider_stream_operational_topic(message: GmailMessageRecord) -> str:
    text = " ".join(
        compact_text(value)
        for value in [
            message.ai_title,
            message.subject,
            message.snippet,
        ]
        if value
    ).lower()
    if re.search(
        r"\b(?:workflow\s+run|run\s+(?:succeeded|failed)|github\s+actions?|pull\s+request|repository|repo|commit|ci|browserslist)\b",
        text,
    ):
        return "developer_automation"
    if re.search(r"\bsponsors?\b", text):
        return "sponsors"
    if re.search(r"\b(?:education|student\s+developer|benefits?)\b", text):
        return "education"
    if re.search(r"\b(?:profile|account|setup|setting\s+up|approval|approved)\b", text):
        return "account"
    return ""


def _is_safe_job_alert_display_cluster(messages: list[GmailMessageRecord], classification: Any, text: str) -> bool:
    if bool(getattr(classification, "requires_user_action", False)) or not _mailbox_text_is_job_alert(text):
        return False
    for message in messages:
        labels = {label.upper() for label in message.label_ids}
        if labels & {"SENT", "DRAFT", "TRASH", "SPAM"}:
            return False
    return True


def _mailbox_display_cluster_family(family: str, text: str) -> str:
    lowered = text.lower()
    if _mailbox_text_is_job_alert(lowered):
        return ""
    return family if family in MAILBOX_DISPLAY_CLUSTER_WORKFLOW_TYPES else ""


def _mailbox_family_from_reference_id(reference_id: str) -> str:
    signal_name, separator, _value = reference_id.partition(":")
    if not separator:
        return ""
    return MAILBOX_REFERENCE_FAMILY_BY_SIGNAL.get(signal_name.lower(), "")


def _mailbox_reference_provider(provider: str, *, reference: str, family: str) -> str:
    signal_name, separator, _value = reference.partition(":")
    if separator and signal_name == "trade_id" and family == "financial_transfer":
        return "fx-retail"
    return provider


def _mailbox_text_is_job_alert(text: str) -> bool:
    lowered = text.lower()
    return "job alert" in lowered or "matches your alert settings" in lowered


def _mailbox_base_cluster_key(cluster_key: str) -> str:
    return cluster_key.split("|", 1)[0]


def _mailbox_cluster_is_provider_stream(cluster_key: str) -> bool:
    return _mailbox_base_cluster_key(cluster_key).startswith("newsletter:provider-stream:")


def _mailbox_cluster_provider_for_title(cluster_key: str, fallback_provider: str) -> str:
    reference = _mailbox_cluster_reference_parts(cluster_key)
    if reference is None:
        return fallback_provider
    provider, _signal_name, _reference_value = reference
    return provider or fallback_provider


def _mailbox_cluster_family(cluster_key: str) -> str | None:
    family = _mailbox_base_cluster_key(cluster_key).split(":", 1)[0].strip()
    return family if family in MAILBOX_DISPLAY_CLUSTER_WORKFLOW_TYPES else None


def _mailbox_display_cluster_id(cluster_key: str) -> str:
    digest = hashlib.sha1(cluster_key.encode("utf-8")).hexdigest()[:20]
    return f"{MAILBOX_DISPLAY_CLUSTER_PREFIX}{digest}"


def _is_mailbox_display_cluster_id(value: str) -> bool:
    return value.startswith(MAILBOX_DISPLAY_CLUSTER_PREFIX)


def _mailbox_cluster_provider_title(messages: list[GmailMessageRecord], provider: str) -> str:
    provider_title = provider.replace("-", " ").title()
    if provider == "fx-retail":
        return "FX Retail"
    if subject_title := _provider_title_from_subjects(messages, provider):
        return subject_title
    domain_title = _provider_title_from_domains(messages, provider)
    if content_title := _provider_title_from_message_titles(messages, domain_title or provider_title):
        return content_title
    if domain_title:
        return domain_title
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
        provider_key = re.sub(r"[^a-z0-9]+", "", provider_title.lower())
        if provider_key in canonical:
            return canonical[provider_key]
        best_key = max(counts, key=lambda key: (counts[key], len(canonical[key])))
        return canonical[best_key]
    return provider_title


def _provider_title_from_domains(messages: list[GmailMessageRecord], provider: str) -> str | None:
    normalized_provider = re.sub(r"[^a-z0-9]+", "", provider.lower())
    if not normalized_provider:
        return None
    candidates: dict[str, tuple[int, str]] = {}
    for message in messages:
        raw_domains: list[str] = []
        _name, address = parseaddr(message.sender or "")
        if "@" in address:
            raw_domains.append(address.rsplit("@", 1)[1])
        signals = message.extracted_signals if isinstance(message.extracted_signals, dict) else {}
        sender_domain_value = signals.get("sender_domain")
        if isinstance(sender_domain_value, str):
            raw_domains.append(sender_domain_value)
        signal_domains = signals.get("domains")
        if isinstance(signal_domains, list):
            raw_domains.extend(str(item) for item in signal_domains if item)
        for raw_domain in raw_domains:
            title = _domain_owner_title_from_address(f"sender@{raw_domain}")
            if not title:
                continue
            normalized_title = re.sub(r"[^a-z0-9]+", "", title.lower())
            if not _provider_title_matches_normalized_provider(normalized_title, normalized_provider):
                continue
            count, canonical = candidates.get(normalized_title, (0, title))
            candidates[normalized_title] = (count + 1, canonical)
    if not candidates:
        return None
    return max(candidates.values(), key=lambda item: (item[0], len(item[1])))[1]


def _provider_title_from_subjects(messages: list[GmailMessageRecord], provider: str) -> str | None:
    normalized_provider = re.sub(r"[^a-z0-9]+", "", provider.lower())
    if not normalized_provider:
        return None
    candidates: dict[str, tuple[int, str]] = {}
    for message in messages:
        subject = _strip_mailbox_subject_prefixes(message.subject or "")
        subject_parts = re.split(r"\s+(?:-|–|—)\s+|:\s+", subject, maxsplit=1)
        if len(subject_parts) < 2:
            continue
        prefix = subject_parts[0]
        prefix = compact_text(prefix)
        if not prefix:
            continue
        words = re.findall(r"[A-Za-z0-9]+", prefix)
        if not 2 <= len(words) <= 6:
            continue
        normalized_prefix = re.sub(r"[^a-z0-9]+", "", prefix.lower())
        if not _provider_title_matches_normalized_provider(normalized_prefix, normalized_provider):
            continue
        key = normalized_prefix
        count, canonical = candidates.get(key, (0, _humanize_title_phrase(prefix)))
        candidates[key] = (count + 1, canonical)
    if not candidates:
        return None
    return max(candidates.values(), key=lambda item: (item[0], len(item[1])))[1]


def _provider_title_from_message_titles(messages: list[GmailMessageRecord], provider_title: str) -> str | None:
    if not _compact_acronym_title(provider_title):
        return None
    candidates: dict[str, tuple[int, str]] = {}
    for message in messages:
        if not (candidate := _provider_title_from_message_content(message, provider_title)):
            continue
        key = re.sub(r"[^a-z0-9]+", "", candidate.lower())
        count, canonical = candidates.get(key, (0, candidate))
        candidates[key] = (count + 1, canonical)
    if not candidates:
        return None
    best_count, best_title = max(candidates.values(), key=lambda item: (item[0], len(item[1])))
    return best_title if best_count >= 2 or best_count == len(messages) else None


def _strip_mailbox_subject_prefixes(value: str) -> str:
    subject = compact_text(value)
    while True:
        next_subject = re.sub(r"(?i)^\s*(?:re|fwd?|fw):\s*", "", subject).strip()
        if next_subject == subject:
            return next_subject
        subject = next_subject


def _provider_title_matches_normalized_provider(candidate: str, provider: str) -> bool:
    if not candidate or not provider:
        return False
    if candidate == provider:
        return True
    shorter, longer = (candidate, provider) if len(candidate) <= len(provider) else (provider, candidate)
    if not longer.startswith(shorter):
        return False
    remainder = longer[len(shorter) :]
    remainder = re.sub(r"^\d+", "", remainder)
    if not remainder:
        return True
    return remainder in DOMAIN_TITLE_SUFFIX_WORDS


def _provider_title_from_sender_name(name: str, provider_title: str) -> str | None:
    if not name or not provider_title:
        return None
    provider_pattern = re.escape(provider_title)
    if match := re.search(rf"(?i)\b{provider_pattern}\b", name):
        return match.group(0)
    normalized_provider = re.sub(r"[^a-z0-9]+", "", provider_title.lower())
    normalized_name = re.sub(r"[^a-z0-9]+", "", name.lower())
    if normalized_provider and normalized_provider in normalized_name:
        return provider_title
    return None


def _mailbox_cluster_title(
    provider_title: str,
    family: str,
    cluster_key: str,
    *,
    messages: list[GmailMessageRecord] | None = None,
) -> str:
    reference_title = _mailbox_cluster_reference_title(provider_title, family, cluster_key, messages=messages or [])
    if reference_title:
        return reference_title
    content_title = _mailbox_cluster_content_title(
        provider_title,
        family,
        cluster_key,
        messages or [],
    )
    if content_title:
        return content_title
    if _mailbox_cluster_is_job_alert(cluster_key):
        return f"{provider_title} job alerts"
    labels = {
        "financial_transfer": "transfer mail",
        "support_case": "support case",
        "billing": "billing mail",
        "logistics": "delivery mail",
        "account_security": "security alerts",
        "application": "application mail",
        "newsletter": "newsletter mail",
        "marketing": "promotions",
    }
    return f"{provider_title} {labels.get(family, 'mail')}"


def _mailbox_cluster_content_title(
    provider_title: str,
    family: str,
    cluster_key: str,
    messages: list[GmailMessageRecord],
) -> str | None:
    if not messages:
        return None
    source_count = len({message.gmail_thread_id or message.message_id for message in messages})
    topic_titles = _mailbox_cluster_topic_titles(messages, provider_title=provider_title, limit=3)
    if not topic_titles:
        return None

    if _mailbox_cluster_is_job_alert(cluster_key):
        job_titles = [_mailbox_cluster_job_alert_title(title) for title in topic_titles]
        if source_count > 1:
            return _mailbox_compact_topic_bundle_title(
                job_titles,
                provider_title=provider_title,
                total_count=source_count,
                label="job alerts",
                max_keywords=2,
            )
        title = _compact_bundle_title(
            job_titles,
            total_count=source_count,
            max_chars=max(48, MAILBOX_BUNDLE_TITLE_MAX_CHARS - len(" job alerts")),
        )
        return f"{title} job alerts" if title else None

    if family in {"newsletter", "marketing"}:
        if source_count > 1:
            if family == "newsletter":
                event_title = _mailbox_event_invitation_bundle_title(
                    topic_titles,
                    provider_title=provider_title,
                    total_count=source_count,
                )
                if event_title:
                    return event_title
            label = _mailbox_bundle_topic_label(
                topic_titles,
                default_label="promotions" if family == "marketing" else "updates",
            )
            if source_count == 2:
                two_item_title = _mailbox_two_item_topic_bundle_title(
                    topic_titles,
                    provider_title=provider_title,
                    label=label,
                )
                if two_item_title:
                    return two_item_title
            return _mailbox_compact_topic_bundle_title(
                topic_titles,
                provider_title=provider_title,
                total_count=source_count,
                label=label,
                max_keywords=2,
            )
        return _compact_bundle_title(topic_titles, total_count=source_count)
    return None


def _mailbox_two_item_topic_bundle_title(
    titles: list[str],
    *,
    provider_title: str,
    label: str,
) -> str | None:
    two_titles = titles[:2]
    if len(two_titles) < 2:
        return None

    joined = _compact_bundle_title(two_titles, total_count=2, max_chars=64)
    if (
        joined
        and " + " not in joined
        and label != "promotions"
        and not any(_mailbox_bundle_topic_keyphrase(title, provider_title=provider_title) for title in two_titles)
        and all(_mailbox_bundle_title_word_count(title, provider_title=provider_title) <= 4 for title in two_titles)
    ):
        return joined

    keywords: list[str] = []
    seen: set[str] = set()
    for title in two_titles:
        title_keywords = _mailbox_bundle_topic_keywords(
            [title],
            provider_title=provider_title,
            max_keywords=1,
        )
        keyword = title_keywords[0] if title_keywords else _mailbox_bundle_fallback_keyword(
            title,
            provider_title=provider_title,
        )
        if not keyword:
            continue
        normalized = keyword.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        keywords.append(keyword)

    if keywords:
        return f"{_natural_join(keywords)} {label}"
    return joined


def _mailbox_event_invitation_bundle_title(
    titles: list[str],
    *,
    provider_title: str,
    total_count: int,
) -> str | None:
    if total_count < 2 or len(titles) < 2 or not _mailbox_all_titles_are_event_invitations(titles):
        return None
    labels: list[str] = []
    seen: set[str] = set()
    for title in titles[:3]:
        label = _mailbox_event_invitation_topic_label(title, provider_title=provider_title)
        if not label:
            continue
        normalized = label.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        labels.append(label)
    if not labels:
        return None
    if len(labels) == 1:
        return f"{labels[0]} invitations"
    return f"{_natural_join(labels[:2])} invitations"


def _mailbox_all_titles_are_event_invitations(titles: list[str]) -> bool:
    if not titles:
        return False
    return all(
        re.search(
            r"(?i)\b(?:event|finale|hackathon|webinar|meetup|conference|invite|invitation|participate|register|registration)\b",
            title,
        )
        for title in titles
    )


def _mailbox_event_invitation_topic_label(title: str, *, provider_title: str) -> str | None:
    cleaned = _strip_provider_title_prefix(compact_text(title), provider_title)
    lowered = cleaned.lower()
    if re.search(r"\bhackathons?\b", lowered):
        return "Hackathon"
    if re.search(r"\bwebinars?\b", lowered):
        return "Webinar"
    if re.search(r"\bmeetups?\b", lowered):
        return "Meetup"
    if re.search(r"\bconferences?\b", lowered):
        return "Conference"
    if re.search(r"\bai\b", lowered) and re.search(r"\b(?:event|finale|summit)\b", lowered):
        return "AI event"
    if re.search(r"\b(?:event|finale|summit)\b", lowered):
        return "Event"
    return None


def _mailbox_bundle_fallback_keyword(title: str, *, provider_title: str) -> str | None:
    provider_roots = {
        _mailbox_cluster_topic_token_root(token)
        for token in _mailbox_bundle_topic_raw_tokens(provider_title)
    }
    for token in _mailbox_bundle_topic_raw_tokens(title):
        normalized = _mailbox_cluster_topic_token_root(token)
        if (
            not normalized
            or normalized in provider_roots
            or normalized in MAILBOX_BUNDLE_FALLBACK_KEYWORD_STOP_WORDS
        ):
            continue
        return _mailbox_bundle_topic_display(token)
    return None


def _mailbox_bundle_title_word_count(title: str, *, provider_title: str) -> int:
    provider_roots = {
        _mailbox_cluster_topic_token_root(token)
        for token in _mailbox_bundle_topic_raw_tokens(provider_title)
    }
    return sum(
        1
        for token in _mailbox_bundle_topic_raw_tokens(title)
        if _mailbox_cluster_topic_token_root(token) not in provider_roots
    )


def _mailbox_cluster_job_alert_title(title: str) -> str:
    cleaned = compact_text(title)
    cleaned = re.sub(r"(?i)^\s*alert\s*:\s*", "", cleaned).strip()
    cleaned = re.sub(r"(?i)^\s*alert\s+for\s+", "", cleaned).strip()
    cleaned = re.sub(r"(?i)^\s*(?:new\s+)?(?:[A-Z][A-Za-z0-9]+\s+)?job alert\s*(?:for|:)?\s*", "", cleaned).strip()
    cleaned = re.sub(r"(?i)^\s*(?:an?|the)\s+", "", cleaned).strip()
    cleaned = re.sub(r"(?i)\s+job posting\s*$", "", cleaned).strip()
    return cleaned or title


def _compact_bundle_title(
    titles: list[str],
    *,
    total_count: int,
    max_chars: int = MAILBOX_BUNDLE_TITLE_MAX_CHARS,
) -> str | None:
    cleaned: list[str] = []
    seen: set[str] = set()
    for title in titles:
        value = compact_text(title).strip(" -–—:|.,")
        if not value:
            continue
        normalized = value.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(value)
    if not cleaned:
        return None

    total_count = max(total_count, len(cleaned))
    max_visible = min(3, len(cleaned))
    for visible_count in range(max_visible, 1, -1):
        remaining = total_count - visible_count
        candidate = "; ".join(cleaned[:visible_count]) if remaining > 0 else _join_bundle_title_parts(cleaned[:visible_count])
        if remaining > 0:
            candidate = f"{candidate} + {remaining} more"
        if len(candidate) <= max_chars:
            return candidate

    first = cleaned[0]
    remaining = total_count - 1
    suffix = f" + {remaining} more" if remaining > 0 else ""
    return f"{_truncate_title(first, max_chars=max_chars - len(suffix))}{suffix}"


def _mailbox_compact_topic_bundle_title(
    titles: list[str],
    *,
    provider_title: str,
    total_count: int,
    label: str,
    max_keywords: int,
) -> str | None:
    keywords = _mailbox_bundle_topic_keywords(
        titles,
        provider_title=provider_title,
        max_keywords=max_keywords,
    )
    if label == "job alerts" and any(keyword.lower() == "ai" for keyword in keywords):
        return "AI job alerts"
    if keywords:
        return f"{_natural_join(keywords)} {label}"
    context_label = _mailbox_bundle_context_label(titles)
    if provider_title and context_label:
        return f"{provider_title} {context_label}"
    if label == "job alerts":
        return "Job alerts"
    return _compact_bundle_title(titles, total_count=total_count)


def _mailbox_bundle_topic_keywords(
    titles: list[str],
    *,
    provider_title: str,
    max_keywords: int,
) -> list[str]:
    provider_tokens = {
        _mailbox_cluster_topic_token_root(token)
        for token in _mailbox_bundle_topic_raw_tokens(provider_title)
    }
    scores: dict[str, tuple[float, str, int]] = {}
    order = 0
    for title_index, title in enumerate(titles):
        raw_tokens = _mailbox_bundle_topic_raw_tokens(title)
        title_seen: set[str] = set()
        keyphrase = _mailbox_bundle_topic_keyphrase(title, provider_title=provider_title)
        if keyphrase:
            order += 1
            normalized_phrase = " ".join(
                _mailbox_cluster_topic_token_root(token)
                for token in _mailbox_bundle_topic_raw_tokens(keyphrase)
                if _mailbox_cluster_topic_token_root(token)
            )
            if normalized_phrase:
                title_seen.update(normalized_phrase.split())
                weight = 1.0 + max(0, len(titles) - title_index) * 0.05
                previous_score, previous_display, previous_order = scores.get(
                    normalized_phrase,
                    (0.0, keyphrase, order),
                )
                scores[normalized_phrase] = (previous_score + weight, previous_display, previous_order)
        for token in raw_tokens:
            order += 1
            normalized = _mailbox_cluster_topic_token_root(token)
            if (
                not normalized
                or normalized in provider_tokens
                or normalized in MAILBOX_BUNDLE_TOPIC_STOP_WORDS
                or normalized in title_seen
            ):
                continue
            title_seen.add(normalized)
            weight = 1.0 + max(0, len(titles) - title_index) * 0.05
            previous_score, previous_display, previous_order = scores.get(
                normalized,
                (0.0, _mailbox_bundle_topic_display(token), order),
            )
            scores[normalized] = (previous_score + weight, previous_display, previous_order)
    ranked = sorted(
        scores.items(),
        key=lambda item: (
            -item[1][0],
            _mailbox_bundle_keyword_acronym_penalty(item[1][1]),
            item[1][2],
            item[1][1].lower(),
        ),
    )
    return [display for _token, (_score, display, _order) in ranked[:max_keywords]]


def _mailbox_bundle_topic_keyphrase(title: str, *, provider_title: str) -> str | None:
    cleaned = _strip_provider_title_prefix(compact_text(title), provider_title)
    if not cleaned:
        return None

    acronym = r"[A-Z][A-Z0-9]{1,}"
    acronym_phrase = rf"{acronym}(?:\s+{acronym}){{0,2}}"
    anchor = r"access|approval|approved|request|status|setup|notice|statement|alert|update"
    patterns = [
        rf"\b(?P<phrase>{acronym_phrase}\s+(?i:{anchor}))\b",
        rf"\b(?i:{anchor})\s+(?:to|for|on|of)?\s*(?P<phrase>{acronym_phrase})\b",
    ]
    provider_roots = {
        _mailbox_cluster_topic_token_root(token)
        for token in _mailbox_bundle_topic_raw_tokens(provider_title)
    }
    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if not match:
            continue
        phrase = compact_text(match.group("phrase")).strip(" -–—:|.,")
        if not phrase:
            continue
        phrase_roots = {
            _mailbox_cluster_topic_token_root(token)
            for token in _mailbox_bundle_topic_raw_tokens(phrase)
        }
        if phrase_roots and phrase_roots <= provider_roots:
            continue
        if re.fullmatch(acronym_phrase, phrase):
            leading_anchor = re.match(rf"(?i)\b(?P<anchor>{anchor})\b", match.group(0))
            if not leading_anchor:
                continue
            phrase = f"{phrase} {leading_anchor.group('anchor').lower()}"
        return _mailbox_bundle_topic_keyphrase_display(phrase)
    return None


def _mailbox_bundle_topic_keyphrase_display(phrase: str) -> str:
    words = _mailbox_bundle_topic_raw_tokens(phrase)
    if not words:
        return phrase
    displayed: list[str] = []
    for word in words:
        display = _mailbox_bundle_topic_display(word)
        if display.isupper():
            displayed.append(display)
        elif displayed:
            displayed.append(display.lower())
        else:
            displayed.append(display)
    return " ".join(displayed)


def _mailbox_bundle_topic_label(titles: list[str], *, default_label: str) -> str:
    text = " ".join(compact_text(title).lower() for title in titles)
    if re.search(r"\b(event|invite|invitation|webinar)\b", text):
        return default_label
    if re.search(r"\b(cashback|coupon|deal|discount|offer|promo|promotion|reward|sale|voucher)\b", text):
        return "promotions"
    return default_label


def _mailbox_bundle_keyword_acronym_penalty(value: str) -> int:
    return 1 if value.isupper() and len(value) <= 3 else 0


def _mailbox_bundle_context_label(titles: list[str]) -> str | None:
    text = " ".join(compact_text(title).lower() for title in titles)
    if re.search(r"\b(tips?|using|how to|get more|power moves?|set up|setup|guide)\b", text):
        return "usage tips"
    return None


def _mailbox_bundle_topic_raw_tokens(value: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9]+", compact_text(value))
    return [
        token.lower()
        for token in tokens
        if len(token) > 2 or token.lower() in {"ai", "fx", "om"}
    ]


def _mailbox_bundle_topic_display(token: str) -> str:
    lowered = token.lower()
    if lowered in DOMAIN_WORD_TITLES:
        return DOMAIN_WORD_TITLES[lowered]
    if lowered in {"ai", "fx", "om", "nds", "kyc", "tls"}:
        return lowered.upper()
    return lowered[:1].upper() + lowered[1:]


def _join_bundle_title_parts(values: list[str]) -> str:
    if any(" and " in value.lower() for value in values):
        return "; ".join(values)
    return _natural_join(values)


def _truncate_title(value: str, *, max_chars: int) -> str:
    cleaned = compact_text(value).strip(" -–—:|.,")
    if max_chars <= 0:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned
    truncated = cleaned[:max_chars].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return truncated.rstrip(" -–—:|.,") or cleaned[:max_chars].rstrip(" -–—:|.,")


def _mailbox_cluster_reference_title(
    provider_title: str,
    family: str,
    cluster_key: str,
    *,
    messages: list[GmailMessageRecord],
) -> str | None:
    reference = _mailbox_cluster_reference_parts(cluster_key)
    if reference is None:
        return None
    _provider, signal_name, reference_value = reference
    display_value = _mailbox_reference_display_value(signal_name, reference_value)
    label = {
        "support_case": "dispute" if signal_name == "dispute_id" else "repair" if signal_name == "repair_id" else "service request" if signal_name == "ticket_id" else "support case",
        "billing": "invoice" if signal_name == "invoice_id" else "billing reference",
        "logistics": "tracking update" if signal_name == "tracking_id" else "delivery reference",
        "application": "application",
        "financial_transfer": "trade" if signal_name == "trade_id" else "transfer reference",
    }.get(family)
    if not label:
        return None
    base = f"{provider_title} {label} {display_value}"
    state = _mailbox_cluster_reference_latest_state(messages)
    if state and state.lower() not in base.lower():
        return f"{base} {state}"
    return base


def _mailbox_cluster_reference_latest_state(messages: list[GmailMessageRecord]) -> str:
    if not messages:
        return ""
    latest = max(messages, key=lambda message: message.internal_date or message.updated_at or "")
    text = " ".join(
        compact_text(value or "")
        for value in [latest.ai_title, latest.subject, latest.snippet, latest.text_body]
        if compact_text(value or "")
    ).lower()
    if not text:
        return ""
    state_patterns = [
        ("out for delivery", r"\bout for delivery\b"),
        ("delivered", r"\b(?:has been |was |is )?delivered\b"),
        ("arriving today", r"\barriving today\b"),
        ("shipped", r"\b(?:has been |was |is )?shipped\b"),
        ("processed", r"\b(?:processed|completed|settled)\b"),
        ("resolved", r"\b(?:resolved|closed)\b"),
        ("approved", r"\bapproved\b"),
        ("rejected", r"\b(?:rejected|declined)\b"),
        ("failed", r"\b(?:failed|failure|unsuccessful)\b"),
        ("under review", r"\b(?:under review|open under review|being reviewed|reviewing|working on it)\b"),
        ("confirmed", r"\b(?:confirmed|confirmation)\b"),
        ("followed up", r"\b(?:followed up|follows up|follow-up|follow up)\b"),
        ("registered", r"\b(?:registered|assigned|reference number|service request number|request number)\b"),
        ("acknowledged", r"\b(?:acknowledged|acknowledgement|acknowledgment)\b"),
        ("received", r"\b(?:received|submitted)\b"),
        ("replied", r"\b(?:replied|responded|response)\b"),
        ("updated", r"\b(?:updated|status update|interim update|update on)\b"),
    ]
    for label, pattern in state_patterns:
        if re.search(pattern, text):
            return label
    return ""


def _mailbox_cluster_reference_parts(cluster_key: str) -> tuple[str, str, str] | None:
    parts = _mailbox_base_cluster_key(cluster_key).split(":")
    if len(parts) < 5 or parts[1] != "ref":
        return None
    provider = parts[2]
    signal_name = parts[3]
    reference_value = ":".join(parts[4:])
    if not provider or not signal_name or not reference_value:
        return None
    return provider, signal_name, reference_value


def _mailbox_cluster_grouping_metadata(
    cluster_key: str,
    *,
    family: str,
    messages: list[GmailMessageRecord],
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source": "mailbox_display_cluster",
        "cluster_key": cluster_key,
        "family": family,
    }
    reference = _mailbox_cluster_reference_parts(cluster_key)
    if reference is None:
        return metadata
    provider, signal_name, reference_value = reference
    direct_message_ids = [
        message.message_id
        for message in messages
        if _mailbox_message_has_direct_reference(message, signal_name=signal_name, reference_value=reference_value)
    ]
    metadata["reference"] = {
        "provider": provider,
        "signal_name": signal_name,
        "value": reference_value,
        "direct_message_count": len(direct_message_ids),
        "message_count": len(messages),
        "direct_message_ids": direct_message_ids[:20],
    }
    return metadata


def _mailbox_message_has_direct_reference(message: GmailMessageRecord, *, signal_name: str, reference_value: str) -> bool:
    signals = message.extracted_signals if isinstance(message.extracted_signals, dict) else {}
    if str(signals.get(signal_name) or "").lower() == reference_value.lower():
        return True
    try:
        facts, _classification = deterministic_classification([message])
    except Exception:
        return False
    return str(facts.reference_id or "").lower() == f"{signal_name}:{reference_value}".lower()


def _mailbox_cluster_summary(
    provider_title: str,
    family: str,
    thread_count: int,
    *,
    cluster_key: str = "",
    messages: list[GmailMessageRecord] | None = None,
) -> str:
    if _mailbox_cluster_is_job_alert(cluster_key):
        return _mailbox_cluster_topic_summary(
            provider_title,
            "job alert",
            thread_count,
            messages or [],
        )
    label = {
        "financial_transfer": "transfer",
        "support_case": "support case",
        "billing": "billing",
        "logistics": "travel or delivery",
        "account_security": "security",
        "application": "application",
        "newsletter": "update",
        "marketing": "product update",
    }.get(family, "mailbox")
    if family not in {"newsletter", "marketing"}:
        reference = _mailbox_cluster_reference_parts(cluster_key)
        if reference is not None:
            return _mailbox_cluster_reference_summary(provider_title, thread_count, reference, messages or [])
        update_label = "update" if thread_count == 1 else "updates"
        return f"{thread_count} {provider_title} {label} {update_label}."
    return _mailbox_cluster_topic_summary(provider_title, label, thread_count, messages or [])


def _mailbox_cluster_reference_summary(
    provider_title: str,
    thread_count: int,
    reference: tuple[str, str, str],
    messages: list[GmailMessageRecord],
) -> str:
    _provider, signal_name, reference_value = reference
    reference_subject = _mailbox_reference_summary_subject(signal_name, reference_value)
    update_label = "update" if thread_count == 1 else "updates"
    base = f"{reference_subject}: {thread_count} {provider_title} {update_label}"
    latest_title = _latest_cluster_message_title(messages)
    if latest_title:
        latest_title = _strip_provider_title_prefix(latest_title, provider_title)
        if latest_title and latest_title[0].islower():
            latest_title = f"{latest_title[0].upper()}{latest_title[1:]}"
        return f"{base}; latest: {_ensure_sentence(latest_title)}"
    return f"{base}."


def _mailbox_reference_summary_subject(signal_name: str, reference_value: str) -> str:
    reference_label = {
        "ticket_id": "Service request",
        "dispute_id": "Dispute",
        "invoice_id": "Invoice",
        "trade_id": "Trade",
        "tracking_id": "Tracking reference",
        "application_id": "Application",
        "booking_id": "Booking",
        "order_id": "Order",
        "repair_id": "Repair",
    }.get(signal_name, "Reference")
    return f"{reference_label} {_mailbox_reference_display_value(signal_name, reference_value)}"


def _mailbox_cluster_topic_summary(
    provider_title: str,
    label: str,
    thread_count: int,
    messages: list[GmailMessageRecord],
) -> str:
    titles = _mailbox_cluster_topic_titles(messages, provider_title=provider_title)
    if not titles:
        return f"{thread_count} {_mailbox_cluster_topic_label(label, thread_count)} from {provider_title}."
    if len(titles) == 1:
        return f"Latest: {_ensure_sentence(titles[0])}"
    return f"Latest: {titles[0]}; also {_ensure_sentence(_natural_join(titles[1:]))}"


def _mailbox_cluster_topic_label(label: str, thread_count: int) -> str:
    if label == "update":
        return "update" if thread_count == 1 else "updates"
    if label == "product update":
        return "product update" if thread_count == 1 else "product updates"
    if label == "job alert":
        return "job alert" if thread_count == 1 else "job alerts"
    return label


def _mailbox_cluster_topic_titles(messages: list[GmailMessageRecord], *, provider_title: str, limit: int = 3) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    for message in sorted(messages, key=lambda item: item.internal_date or item.updated_at or "", reverse=True):
        title = _mailbox_cluster_topic_title(message, provider_title=provider_title)
        if not title:
            continue
        normalized = title.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        titles.append(title)
        if len(titles) >= limit:
            break
    if len(titles) < min(limit, len(messages)):
        for message in sorted(messages, key=lambda item: item.internal_date or item.updated_at or "", reverse=True):
            title = _mailbox_cluster_fallback_topic_title(message, provider_title=provider_title)
            if not title:
                continue
            normalized = title.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            titles.append(title)
            if len(titles) >= limit:
                break
    return titles


def _mailbox_cluster_fallback_topic_title(message: GmailMessageRecord, *, provider_title: str) -> str:
    title = compact_text(message.subject or message.snippet or "")
    if not title:
        return ""
    title = _strip_provider_title_prefix(title, provider_title)
    title = re.sub(r"\s+", " ", title).strip(" -–—:|.,")
    if not title or title.lower() in {"update", "updates", "newsletter", "notification", "notifications", "mail"}:
        return ""
    if title[0].islower():
        title = f"{title[0].upper()}{title[1:]}"
    return title[:120].rstrip(" -–—:|.,")


def _mailbox_cluster_topic_title(message: GmailMessageRecord, *, provider_title: str) -> str:
    title = _mailbox_cluster_topic_source_title(message, provider_title=provider_title)
    if not title:
        return ""
    title = _strip_provider_title_prefix(title, provider_title)
    title = re.sub(r"\s+", " ", title).strip(" -–—:|.,")
    if not title or title.lower() in {"update", "updates", "newsletter", "notification", "notifications", "mail"}:
        return ""
    if title[0].islower():
        title = f"{title[0].upper()}{title[1:]}"
    return title[:120].rstrip(" -–—:|.,")


def _mailbox_cluster_topic_source_title(message: GmailMessageRecord, *, provider_title: str) -> str:
    ai_title = compact_text(message.ai_title or "")
    fallback_title = compact_text(message.subject or message.snippet or "")
    if ai_title and not _mailbox_cluster_topic_ai_title_is_weak(
        ai_title,
        subject=message.subject or "",
        snippet=message.snippet or "",
        provider_title=provider_title,
    ):
        return ai_title
    return fallback_title or ai_title


def _mailbox_cluster_topic_ai_title_is_weak(
    ai_title: str,
    *,
    subject: str,
    snippet: str,
    provider_title: str,
) -> bool:
    title = compact_text(ai_title)
    if not title:
        return True
    normalized = title.lower().strip(" -–—:|.,")
    if normalized.startswith("generic "):
        return True
    title_tokens = _mailbox_cluster_topic_title_tokens(title)
    if not title_tokens:
        return True
    source_text = " ".join(value for value in [subject, snippet] if value)
    if not compact_text(source_text):
        return False
    source_tokens = _mailbox_cluster_topic_title_tokens(source_text)
    provider_tokens = _mailbox_cluster_topic_title_tokens(provider_title)
    if title_tokens & (source_tokens | provider_tokens):
        return False
    raw_title_tokens = _mailbox_cluster_topic_raw_tokens(title)
    has_generic_marker = bool(raw_title_tokens & MAILBOX_TOPIC_TITLE_GENERIC_WORDS)
    return len(title_tokens) <= 1 or (has_generic_marker and len(title_tokens) <= 2)


def _mailbox_cluster_topic_title_tokens(value: str) -> set[str]:
    return {
        normalized
        for token in _mailbox_cluster_topic_raw_tokens(value)
        if (normalized := _mailbox_cluster_topic_token_root(token)) not in MAILBOX_TOPIC_TITLE_GENERIC_WORDS
    }


def _mailbox_cluster_topic_raw_tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", compact_text(value).lower()) if len(token) > 2}


def _mailbox_cluster_topic_token_root(token: str) -> str:
    if len(token) > 6 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 5 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 5 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 4 and token.endswith("s"):
        return token[:-1]
    return token


def _strip_provider_title_prefix(title: str, provider_title: str) -> str:
    provider = compact_text(provider_title)
    if not provider:
        return title
    pattern = re.compile(rf"(?i)^\s*{re.escape(provider)}\s*(?:[-–—:|,]\s*)?")
    stripped = pattern.sub("", title, count=1).strip()
    return stripped or title


def _natural_join(values: list[str]) -> str:
    cleaned = [value for value in values if value]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return f"{', '.join(cleaned[:-1])}, and {cleaned[-1]}"


def _mailbox_reference_display_value(signal_name: str, reference_value: str) -> str:
    if signal_name in {"dispute_id", "trade_id"}:
        return reference_value.upper()
    return reference_value


def _mailbox_cluster_is_job_alert(cluster_key: str) -> bool:
    return _mailbox_base_cluster_key(cluster_key).endswith(":job-alert")


def _latest_cluster_message_title(messages: list[GmailMessageRecord]) -> str:
    if not messages:
        return ""
    latest = max(messages, key=lambda message: message.internal_date or message.updated_at or "")
    return compact_text(latest.ai_title or latest.subject or latest.snippet or "")[:180]


def _ensure_sentence(value: str) -> str:
    cleaned = compact_text(value)
    if not cleaned:
        return ""
    if cleaned[-1] in ".!?":
        return cleaned
    return f"{cleaned}."


def _cluster_action_type(family: str) -> str:
    if family == "billing":
        return "pay"
    if family == "logistics":
        return "track"
    if family == "account_security":
        return "review"
    return "open"


def _mailbox_cluster_priority(cluster_key: str, *, family: str, classification: Any) -> int:
    if _mailbox_cluster_is_job_alert(cluster_key) or family in {"newsletter", "marketing"}:
        return 0
    if family == "billing":
        return 74
    if family == "account_security":
        return 74 if getattr(classification, "requires_user_action", False) else 58
    if _mailbox_cluster_reference_parts(cluster_key) is not None:
        if family in {"support_case", "financial_transfer", "application"}:
            return 48
        if family == "logistics":
            return 40
    if getattr(classification, "requires_user_action", False):
        return 58
    return 0


def _sort_mailbox_rows(rows: list[GmailThreadRow]) -> list[GmailThreadRow]:
    return sorted(rows, key=lambda row: (row.latest_received_at or "", row.thread_id), reverse=True)


def _gmail_child_row_from_message(message: GmailMessageRecord, *, mailbox_label: str = "inbox") -> GmailThreadChildRow:
    return GmailThreadChildRow(
        message_id=message.message_id,
        gmail_thread_id=message.gmail_thread_id,
        sender=_mailbox_display_sender(message, mailbox_label),
        subject=message.subject,
        ai_title=message.ai_title,
        snippet=message.snippet,
        received_at=message.internal_date or message.updated_at,
        label_ids=message.label_ids,
        labels=message.label_ids,
        unread="UNREAD" in {label.upper() for label in message.label_ids},
    )


def _presentation_status(
    ai_group: MailGroupRecord | None,
    *,
    messages: list[GmailMessageRecord] | None = None,
    display_sender: str | None = None,
    display_title: str | None = None,
) -> str:
    if ai_group is None:
        return "fallback"
    if ai_group.enrichment_status == "pending":
        return "ai_pending"
    title = compact_text(display_title or ai_group.ai_title or "")
    if (
        ai_group.enrichment_status == "ready"
        and title
        and _ai_group_display_title_is_specific(title, messages or [], display_sender=display_sender)
    ):
        return "ai_ready"
    return "fallback"


def _ai_group_display_title_is_specific(
    title: str,
    messages: list[GmailMessageRecord],
    *,
    display_sender: str | None,
) -> bool:
    cleaned = compact_text(title)
    if not cleaned or _display_title_is_vague(cleaned):
        return False
    if not messages:
        return True
    source_text = _ai_group_title_source_text(messages)
    provider_title = compact_text(display_sender or _summary_provider_name(messages[-1]))
    if _mailbox_cluster_topic_ai_title_is_weak(
        cleaned,
        subject=source_text,
        snippet="",
        provider_title=provider_title,
    ):
        return False
    source_tokens = _mailbox_cluster_topic_title_tokens(source_text)
    if not source_tokens:
        return True
    provider_tokens = _mailbox_cluster_topic_title_tokens(provider_title)
    title_tokens = _mailbox_cluster_topic_title_tokens(cleaned)
    source_task_tokens = source_tokens - provider_tokens
    title_task_tokens = title_tokens - provider_tokens
    return bool(source_task_tokens and title_task_tokens and source_task_tokens & title_task_tokens)


def _ai_group_title_source_text(messages: list[GmailMessageRecord]) -> str:
    parts: list[str] = []
    for message in messages:
        parts.extend([message.subject or "", message.snippet or "", message.text_body or ""])
        signals = message.extracted_signals if isinstance(message.extracted_signals, dict) else {}
        for value in signals.values():
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, (int, float)):
                parts.append(str(value))
            elif isinstance(value, list):
                parts.extend(str(item) for item in value if isinstance(item, (str, int, float)))
    return compact_text(" ".join(compact_text(part) for part in parts if part))


def _message_matches_mailbox_label(message: GmailMessageRecord, label: str) -> bool:
    labels = {item.upper() for item in message.label_ids}
    if label == "all":
        return True
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
    if sender := _non_generic_sender_display(message.sender):
        if content_title := _provider_title_from_message_content(message, sender):
            return content_title
        return sender
    return message.sender


def _non_generic_sender_display(sender: str | None) -> str | None:
    name, address = parseaddr(sender or "")
    display_name = compact_text(name)
    normalized_name = re.sub(r"[^a-z0-9]+", "", display_name.lower())
    if display_name and normalized_name not in GENERIC_SENDER_SLUGS:
        if domain_owner := _compressed_display_name_domain_owner(display_name, address):
            return domain_owner
        if (domain_owner := _domain_owner_title_from_address(address)) and _display_name_is_domain_owner_service_channel(
            display_name,
            domain_owner,
        ):
            return domain_owner
        return display_name
    return _domain_owner_title_from_address(address)


def _compressed_display_name_domain_owner(display_name: str, address: str) -> str | None:
    if re.search(r"\s", display_name):
        return None
    if "." in display_name:
        return None
    domain_owner = _domain_owner_title_from_address(address)
    if not domain_owner:
        return None
    normalized_display = re.sub(r"[^a-z0-9]+", "", display_name.lower())
    normalized_owner = re.sub(r"[^a-z0-9]+", "", domain_owner.lower())
    owner_words = re.findall(r"[A-Za-z0-9]+", domain_owner)
    first_owner_word = owner_words[0].lower() if owner_words else ""
    owner_initials = "".join(word[0].lower() for word in owner_words if word)
    if normalized_display == normalized_owner:
        return domain_owner
    if first_owner_word and normalized_display.startswith(first_owner_word):
        return domain_owner
    if owner_initials and normalized_display.startswith(owner_initials) and len(normalized_display) <= 5:
        return domain_owner
    display_words = _domain_owner_label_words(display_name)
    if display_words and all(word in SERVICE_CHANNEL_WORDS for word in display_words):
        return domain_owner
    return None


def _display_name_is_domain_owner_service_channel(display_name: str, domain_owner: str) -> bool:
    display_tokens = re.findall(r"[A-Za-z0-9]+", display_name)
    owner_tokens = re.findall(r"[A-Za-z0-9]+", domain_owner)
    if len(display_tokens) < 2 or not owner_tokens:
        return False
    first_token = display_tokens[0].lower()
    owner_words = [word.lower() for word in owner_tokens]
    first_token_words = _domain_owner_label_words(first_token)
    owner_key = "".join(owner_words)
    first_key = "".join(first_token_words)
    owner_initials = "".join(word[0] for word in owner_words if word)
    owner_matches = (
        first_key == owner_key
        or first_token == owner_initials
        or (bool(owner_initials) and first_token.startswith(owner_initials) and len(first_token) <= 5)
    )
    if not owner_matches:
        return False
    return all(token.lower() in SERVICE_CHANNEL_WORDS for token in display_tokens[1:])


def _domain_owner_title_from_address(address: str) -> str | None:
    if "@" not in address:
        return None
    domain = address.rsplit("@", 1)[1].lower()
    labels = [label for label in domain.split(".") if label]
    if len(labels) < 2:
        return None
    owner_labels = labels[:-1]
    if len(owner_labels) > 1 and owner_labels[-1] in {"com", "co", "org", "net", "edu", "gov"}:
        owner_labels = owner_labels[:-1]
    if len(owner_labels) > 1 and owner_labels[-1] in DOMAIN_PLATFORM_LABELS:
        owner_labels = owner_labels[:-1]
    while len(owner_labels) > 1 and owner_labels[0] in DOMAIN_SERVICE_LABELS:
        owner_labels = owner_labels[1:]
    if not owner_labels:
        return None
    words: list[str] = []
    for label in owner_labels:
        if label in GENERIC_SENDER_SLUGS:
            continue
        for token in _domain_owner_label_words(label):
            if token in GENERIC_SENDER_SLUGS:
                continue
            if not words or words[-1] != token:
                words.append(token)
    if not words:
        return None
    return " ".join(_humanize_domain_owner_word(word) for word in words)


def _domain_owner_label_words(label: str) -> list[str]:
    base_parts = re.findall(r"[a-z0-9]+", label.lower())
    words: list[str] = []
    for part in base_parts:
        if part in DOMAIN_LABEL_WORDS:
            words.extend(DOMAIN_LABEL_WORDS[part])
            continue
        pending: list[str] = []
        remainder = part
        while remainder:
            suffix = next(
                (
                    candidate
                    for candidate in DOMAIN_OWNER_SUFFIX_WORDS
                    if remainder.endswith(candidate) and remainder != candidate and len(remainder) - len(candidate) >= 3
                ),
                None,
            )
            if suffix is None:
                pending.append(remainder)
                break
            pending.append(suffix)
            remainder = remainder[: -len(suffix)]
        words.extend(reversed(pending))
    return words


def _humanize_compact_provider_name(value: str) -> str:
    if not value or re.search(r"[\s@<]", value):
        return value
    words = _domain_owner_label_words(value)
    if len(words) < 2:
        return value
    return " ".join(_humanize_domain_owner_word(word) for word in words)


def _humanize_title_phrase(value: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", value)
    return " ".join(word.upper() if word.isupper() and len(word) <= 6 else word[:1].upper() + word[1:] for word in words)


def _provider_title_from_message_content(message: GmailMessageRecord, fallback_title: str) -> str | None:
    if not _compact_acronym_title(fallback_title):
        return None
    for value in (message.ai_title, message.subject, message.snippet):
        if candidate := _leading_title_phrase(value or "", fallback_title):
            return candidate
    return None


def _compact_acronym_title(value: str) -> bool:
    letters = re.sub(r"[^A-Za-z]+", "", value or "")
    return 2 <= len(letters) <= 5 and letters.upper() == letters


def _leading_title_phrase(value: str, fallback_title: str) -> str | None:
    text = _strip_mailbox_subject_prefixes(value)
    match = re.match(r"^\s*((?:[A-Z][a-zA-Z]+|[A-Z]{2,})(?:\s+(?:[A-Z][a-zA-Z]+|[A-Z]{2,})){1,3})\b", text)
    if not match:
        return None
    words = re.findall(r"[A-Za-z0-9]+", match.group(1))
    while len(words) > 2 and words[-1].lower() in TITLE_PHRASE_STOP_STARTS:
        words.pop()
    if len(words) < 2 or words[0].lower() in TITLE_PHRASE_STOP_STARTS:
        return None
    if words[0].lower() in {"dear", "hello", "hi", "hey"}:
        return None
    fallback_words = {word.lower() for word in re.findall(r"[A-Za-z0-9]+", fallback_title)}
    if any(word.lower() in fallback_words for word in words):
        return None
    fallback_key = re.sub(r"[^a-z0-9]+", "", fallback_title.lower())
    candidate = " ".join(words)
    candidate_key = re.sub(r"[^a-z0-9]+", "", candidate.lower())
    if not candidate_key or candidate_key == fallback_key:
        return None
    return _humanize_title_phrase(candidate)


def _humanize_domain_owner_word(word: str) -> str:
    if title := DOMAIN_WORD_TITLES.get(word.lower()):
        return title
    if word.lower() in {"bank"}:
        return word.title()
    return word.upper() if len(word) <= 4 else word.title()


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
        attachments=gmail_attachments_for_message(message),
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


def _fetch_body_for_reader_now(
    settings: Settings,
    *,
    user_id: str,
    group_id: str = "",
    gmail_thread_id: str = "",
) -> bool:
    try:
        from app.services.gmail_importer import run_gmail_body_fetch

        return run_gmail_body_fetch(
            settings,
            user_id=user_id,
            group_id=group_id,
            gmail_thread_id=gmail_thread_id,
        ) > 0
    except Exception:
        return False


def _group_needs_body_warmup(messages: list[GmailMessageRecord]) -> bool:
    return any((message.body_fetch_status or "missing") != "fetched" for message in messages)


def _enqueue_body_fetch_for_groups(
    settings: Settings,
    *,
    user_id: str,
    group_ids: list[str],
    priority: int,
    limit: int,
) -> None:
    for group_id in list(dict.fromkeys([item for item in group_ids if item]))[:limit]:
        _enqueue_body_fetch_for_group(settings, user_id=user_id, group_id=group_id, priority=priority)


def _enqueue_body_fetch_for_mailbox_rows(
    settings: Settings,
    *,
    user_id: str,
    mailbox: MailboxResponse,
    limit: int,
    priority: int,
) -> None:
    group_ids: list[str] = []
    thread_ids: list[str] = []
    for section in mailbox.sections:
        for row in section.rows:
            if row.ai_group_id:
                group_ids.append(row.ai_group_id)
            elif _is_mailbox_display_cluster_id(row.thread_id):
                thread_ids.extend(
                    child.gmail_thread_id
                    for child in row.children
                    if child.gmail_thread_id
                )
            elif row.thread_id:
                thread_ids.append(row.thread_id)
            if len(group_ids) + len(thread_ids) >= limit:
                break
        if len(group_ids) + len(thread_ids) >= limit:
            break
    for group_id in list(dict.fromkeys(group_ids))[:limit]:
        _enqueue_body_fetch_for_group(settings, user_id=user_id, group_id=group_id, priority=priority)
    remaining = max(0, limit - len(set(group_ids)))
    for thread_id in list(dict.fromkeys(thread_ids))[:remaining]:
        _enqueue_body_fetch_for_gmail_thread(settings, user_id=user_id, gmail_thread_id=thread_id, priority=priority)


def _enqueue_body_fetch_for_smart_inbox(
    settings: Settings,
    *,
    user_id: str,
    smart_inbox: SmartInboxResponse,
    limit: int,
    priority: int,
) -> list[str]:
    thread_ids: list[str] = []
    for section in smart_inbox.sections:
        for row in section.rows:
            if row.offline_status != "partial":
                continue
            thread_ids.extend(row.source_thread_ids)
            if len(thread_ids) >= limit:
                break
        if len(thread_ids) >= limit:
            break
    job_ids: list[str] = []
    for thread_id in list(dict.fromkeys(thread_ids))[:limit]:
        job_ids.append(_enqueue_body_fetch_for_gmail_thread(settings, user_id=user_id, gmail_thread_id=thread_id, priority=priority))
    return job_ids


def _enqueue_body_fetch_for_group(settings: Settings, *, user_id: str, group_id: str, priority: int) -> str:
    job = enqueue_job(
        str(settings.database_path),
        kind="gmail_body_fetch",
        queue="reader",
        user_id=user_id,
        dedupe_key=f"gmail-body-fetch:{user_id}:{group_id}",
        priority=priority,
        payload={"user_id": user_id, "group_id": group_id},
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
    )
    return job.id


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
    return normalized if normalized in {"inbox", "sent", "drafts", "spam", "trash", "archive", "all"} else "inbox"  # type: ignore[return-value]


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
    if label == "archive":
        return not ({"INBOX", "SENT", "DRAFT", "SPAM", "TRASH"} & labels)
    return True
