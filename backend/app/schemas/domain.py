from __future__ import annotations

"""Core API response models for auth, dashboard, Gmail, jobs, and app session state."""

from typing import Any, Literal

from pydantic import BaseModel, Field


SourceType = Literal["gmail", "calendar", "manual"]
NeedType = Literal["decision", "awareness"]
ActionType = Literal["inline", "external", "none"]
EffortLevel = Literal["quick", "deep"]
TimingBand = Literal["now", "today", "later", "hidden"]
ActionConfidence = Literal["high", "medium", "low"]
LifecycleState = Literal["active", "scheduled", "resolved", "suppressed"]
EntityCurrentState = Literal["open", "waiting", "done"]
GmailThreadAction = Literal["archive", "unarchive", "mark_read", "move_trash", "delete_forever"]
QueuedThreadActionState = Literal["queued", "applying", "applied", "failed"]
MailSendState = Literal["queued", "sending", "sent", "failed", "reauth_required"]
MailboxLabel = Literal["inbox", "sent", "drafts", "spam", "trash", "archive", "all"]
JobStatus = Literal["queued", "running", "succeeded", "failed"]
ThreadMessageReaderMarkerKind = Literal["external_warning", "classification"]

class SourceRecord(BaseModel):
    """Normalized source record entering the backend pipeline."""

    id: str
    user_id: str
    source: SourceType
    thread_id: str
    raw_payload: dict[str, Any]
    received_at: str


class AttentionItemDetail(BaseModel):
    """Human-facing detail copy shared by web and iOS clients."""

    body: list[str] = Field(default_factory=list)
    action_label: str
    source_label: str
    action_url: str | None = None


class AttentionItem(BaseModel):
    """User-facing feed item returned to the frontend."""

    id: str
    entity_id: str
    user_id: str
    need_type: NeedType
    action_type: ActionType
    effort_level: EffortLevel
    timing_band: TimingBand
    action_confidence: ActionConfidence
    primary_action: str
    fallback_action: str
    title: str
    why_this_is_here: str
    detail: AttentionItemDetail | None = None
    due_at: str | None = None
    importance_level: Literal["high", "medium", "low"] | None = None
    lifecycle_state: str | None = None
    current_state: EntityCurrentState | None = None
    source: SourceType | None = None
    gmail_thread_id: str | None = None
    gmail_thread_action: GmailThreadAction | None = None
    trace_id: str
    created_at: str


class PipelineEntity(BaseModel):
    """Canonical entity shape used during feed construction."""

    id: str
    user_id: str
    source: SourceType | None = None
    current_state: EntityCurrentState
    due_at: str | None
    importance: bool
    lifecycle_state: LifecycleState
    created_at: str
    updated_at: str
    thread_id: str | None = None
    group_id: str | None = None


class PipelineOutput(BaseModel):
    """Per-entity pipeline result before sectioning and ranking."""

    entity: PipelineEntity
    attention_item: AttentionItem | None
    suppressed: bool
    suppression_reason: str | None


class FeedResponse(BaseModel):
    """Final sectioned response consumed by the web dashboard."""

    now: list[AttentionItem] = Field(default_factory=list)
    today: list[AttentionItem] = Field(default_factory=list)
    worth_knowing: list[AttentionItem] = Field(default_factory=list)


class GoogleAuthState(BaseModel):
    """Resolved Google connection status for the local app."""

    available: bool
    connected: bool
    connect_url: str | None = None
    can_send_mail: bool = False
    missing_scopes: list[str] = Field(default_factory=list)


class AuthUserResponse(BaseModel):
    """Current app user returned to authenticated clients."""

    id: str
    email: str
    display_name: str | None = None
    access_enabled: bool = True


class AuthMeResponse(BaseModel):
    """Current app session state."""

    authenticated: bool
    user: AuthUserResponse | None = None


class MobileSessionExchangeRequest(BaseModel):
    """One-time iOS login code exchange request."""

    login_code: str


class MobileSessionExchangeResponse(BaseModel):
    """Bearer session issued to iOS after Google OAuth."""

    session_token: str
    expires_at: str
    user: AuthUserResponse


class DashboardProfile(BaseModel):
    """Identity information resolved from the connected Google account."""

    email: str | None = None
    display_name: str | None = None


class DashboardBriefing(BaseModel):
    """Natural-language top summary for the dashboard."""

    headline: str
    brief: str
    parts: list[dict[str, Any]] = Field(default_factory=list)
    important: dict[str, Any] | None = None
    calendar_availability: dict[str, Any] | None = None


class DashboardResponse(BaseModel):
    """Full dashboard payload consumed by the web app."""

    auth: GoogleAuthState
    profile: DashboardProfile | None = None
    briefing: DashboardBriefing | None = None
    feed: FeedResponse = Field(default_factory=FeedResponse)
    runtime_status: dict[str, Any] = Field(default_factory=dict)



class FirstRunImportJobResponse(BaseModel):
    """Durable status for first-login Gmail and dashboard setup."""

    id: str
    user_id: str
    status: JobStatus
    stage: str
    fetched_count: int
    total_count: int | None = None
    thread_count: int
    dashboard_item_count: int
    inbox_ready_at: str | None = None
    first_groups_ready_at: str | None = None
    dashboard_ready_at: str | None = None
    canonical_dashboard_ready_at: str | None = None
    quality_status: Literal["pending", "ready", "failed"] = "pending"
    quality_error: str | None = None
    full_import_started_at: str | None = None
    full_import_completed_at: str | None = None
    error_message: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str

    @property
    def ready(self) -> bool:
        return self.inbox_ready_at is not None and self.first_groups_ready_at is not None and self.dashboard_ready_at is not None


class PostLoginReadinessResponse(BaseModel):
    """Product readiness contract for the post-login holding screen."""

    mode: Literal["returning", "first_time"]
    stage: Literal[
        "welcome_back",
        "starting_full_import",
        "importing_recent_gmail",
        "grouping_threads",
        "writing_titles",
        "building_dashboard",
        "ready",
        "failed",
    ]
    ready_to_enter: bool
    dashboard_ready: bool
    mailbox_ready: bool
    ready_dashboard_count: int
    ready_mail_group_count: int
    full_import_running: bool
    full_import_completed: bool
    user_display_name: str | None = None
    error_message: str | None = None


class GmailThreadUpdate(BaseModel):
    """One compact raw email event inside a grouped Gmail thread."""

    source_record_id: str
    received_at: str
    subject: str | None = None
    sender: str | None = None
    summary: str | None = None


class GmailThreadMutationResponse(BaseModel):
    """Acknowledgement for a Gmail thread mutation."""

    thread_id: str
    action: GmailThreadAction


class QueuedThreadActionRequest(BaseModel):
    """Client-generated mailbox action that can be queued and replayed."""

    client_action_id: str
    mailbox_thread_id: str
    target_message_id: str | None = None
    action: GmailThreadAction
    created_at: str


class QueuedThreadActionResponse(BaseModel):
    """Queued/applied state for an offline-capable mailbox action."""

    client_action_id: str
    server_action_id: str
    mailbox_thread_id: str
    target_message_id: str | None = None
    action: GmailThreadAction
    state: QueuedThreadActionState
    queued_at: str
    applied_at: str | None = None
    error: str | None = None


class MailComposeRequest(BaseModel):
    """Native compose payload sent through Gmail."""

    client_send_id: str
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str
    body_text: str
    body_html: str | None = None
    created_at: str


class MailReplyRequest(BaseModel):
    """Native reply payload for an existing mailbox conversation."""

    client_send_id: str
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    body_text: str
    body_html: str | None = None
    created_at: str


class MailSendResponse(BaseModel):
    """Durable send status returned to native compose/reply UI."""

    client_send_id: str
    server_send_id: str | None = None
    mailbox_thread_id: str | None = None
    gmail_thread_id: str | None = None
    gmail_message_id: str | None = None
    state: MailSendState
    queued_at: str | None = None
    sent_at: str | None = None
    error: str | None = None
    reauth_url: str | None = None


class TaskCreateRequest(BaseModel):
    """Create a backend-owned manual task."""

    title: str
    notes: str | None = None
    section: Literal["now", "today", "later"] = "today"
    due_at: str | None = None


class TaskUpdateRequest(BaseModel):
    """Patch a backend-owned manual task."""

    title: str | None = None
    notes: str | None = None
    section: Literal["now", "today", "later"] | None = None
    due_at: str | None = None
    status: Literal["open", "done"] | None = None


class TaskResponse(BaseModel):
    """Backend-owned manual task response."""

    id: str
    user_id: str
    entity_id: str
    title: str
    notes: str | None = None
    section: Literal["now", "today", "later"]
    due_at: str | None = None
    status: Literal["open", "done"]
    created_at: str
    updated_at: str


class EntityOutcomeRequest(BaseModel):
    """Explicit app-state outcome for one entity."""

    note: str | None = None


class EntitySnoozeRequest(EntityOutcomeRequest):
    """Snooze an entity until a backend-owned timestamp."""

    snooze_until: str


class EntityOutcomeResponse(BaseModel):
    """Persisted entity outcome."""

    id: str
    user_id: str
    entity_id: str
    outcome_type: Literal["complete", "snooze", "dismiss"]
    snooze_until: str | None = None
    note: str | None = None
    created_at: str


class GmailThreadRow(BaseModel):
    """One Gmail-like conversation row grouped by Gmail thread id."""

    thread_id: str
    entity_id: str | None = None
    title: str | None = None
    href: str | None = None
    latest_source_record_id: str
    latest_received_at: str
    latest_message_at: str | None = None
    latest_subject: str | None = None
    latest_sender: str | None = None
    sender: str | None = None
    participants: list[str] = Field(default_factory=list)
    message_count: int
    summary: str | None = None
    ai_group_id: str | None = None
    ai_title: str | None = None
    ai_summary: str | None = None
    snippet: str | None = None
    label_ids: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    unread: bool = False
    action_needed: bool = False
    action_type: Literal["pay", "reply", "confirm", "track", "review", "read", "open", "none"] = "none"
    action_type_key: Literal["pay", "reply", "confirm", "track", "review", "read", "open", "none"] = "none"
    priority: int = 0
    dashboard_visible: bool = False
    current_state: EntityCurrentState | None = None
    lifecycle_state: LifecycleState | None = None
    outcome_type: Literal["complete", "snooze", "dismiss"] | None = None
    lifecycle_updates: list[GmailThreadUpdate] = Field(default_factory=list)
    children: list["GmailThreadChildRow"] = Field(default_factory=list)
    enrichment_status: Literal["pending", "ready", "failed"] = "ready"
    presentation_status: Literal["ai_ready", "ai_pending", "fallback"] = "ai_ready"
    pending_action: GmailThreadAction | None = None


class GmailThreadChildRow(BaseModel):
    """Lightweight source message row shown under an expanded mailbox thread."""

    message_id: str
    gmail_thread_id: str | None = None
    sender: str | None = None
    subject: str | None = None
    ai_title: str | None = None
    snippet: str | None = None
    received_at: str
    label_ids: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    unread: bool = False


class GmailThreadSection(BaseModel):
    """A non-overlapping date bucket for Gmail-like thread rows."""

    id: str
    title: str
    rows: list[GmailThreadRow] = Field(default_factory=list)


class GmailViewResponse(BaseModel):
    """Raw Gmail-first view grouped into familiar Gmail-like date buckets."""

    total_threads: int
    sections: list[GmailThreadSection] = Field(default_factory=list)


class MailboxResponse(BaseModel):
    """Label-filtered Gmail mailbox response backed by local Gmail snapshots."""

    label: MailboxLabel
    total_threads: int
    next_cursor: str | None = None
    loaded_threads: int = 0
    window_days: int | None = None
    sections: list[GmailThreadSection] = Field(default_factory=list)
    ready_count: int = 0
    pending_count: int = 0
    oldest_imported_at: str | None = None
    full_import_running: bool = False
    full_import_completed: bool = False


class MailboxSyncStateResponse(BaseModel):
    """Current local Gmail sync/watch status for UI cache reconciliation."""

    connected: bool
    last_history_id: str | None = None
    last_full_sync_at: str | None = None
    watch_expiration_at: str | None = None
    last_sync_started_at: str | None = None
    last_sync_completed_at: str | None = None
    last_sync_error: str | None = None
    watch_status: str | None = None
    last_delta_sync_at: str | None = None
    last_poll_at: str | None = None
    poller_online: bool | None = None
    mailbox_revision: str | None = None
    total_threads: int = 0
    full_import_running: bool = False
    full_import_completed: bool = False
    full_import_completed_at: str | None = None
    pending_action_count: int = 0
    last_action_sync_at: str | None = None
    last_action_error: str | None = None
    last_ai_error: str | None = None


class MailboxSyncTriggerResponse(BaseModel):
    """Acknowledgement for a queued mailbox sync request."""

    status: Literal["queued", "synced", "not_connected"]
    state: MailboxSyncStateResponse
    job_id: str | None = None
    queued_at: str | None = None


class ThreadMessageReaderMarker(BaseModel):
    """Compact reader metadata extracted from noisy email body chrome."""

    kind: ThreadMessageReaderMarkerKind
    label: str
    text: str


class ThreadMessageReader(BaseModel):
    """Clean reader projection for native thread detail views."""

    primary_text: str
    markers: list[ThreadMessageReaderMarker] = Field(default_factory=list)
    signature_text: str | None = None
    quoted_text: str | None = None
    footer_text: str | None = None
    original_html_available: bool = False


class ThreadMessage(BaseModel):
    """One message inside a mail group detail timeline."""

    id: str
    source: SourceType
    thread_id: str | None = None
    from_address: str | None = None
    to: str | None = None
    cc: str | None = None
    bcc: str | None = None
    subject: str | None = None
    body: str
    html_body: str | None = None
    html_render_document: str | None = None
    reader: ThreadMessageReader | None = None
    snippet: str | None = None
    label_ids: list[str] = Field(default_factory=list)
    received_at: str


class ThreadReaderResponse(BaseModel):
    """Detail payload for one product-ready mail group."""

    entity_id: str
    user_id: str
    source: SourceType | None = None
    gmail_thread_id: str | None = None
    subject: str | None = None
    title: str | None = None
    summary: str | None = None
    total_messages: int
    limit: int
    offset: int
    has_more: bool
    messages: list[ThreadMessage] = Field(default_factory=list)


class BackgroundJobResponse(BaseModel):
    """User/admin visible durable job status."""

    id: str
    kind: str
    queue: str
    status: str
    user_id: str | None = None
    attempt_count: int
    max_attempts: int
    last_error: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str


class OpsHealthResponse(BaseModel):
    """Small operational snapshot for production debugging."""

    queue_depth: dict[str, int] = Field(default_factory=dict)
    dead_jobs: int = 0
    stale_running_jobs: int = 0
    oldest_queued_age_seconds: int | None = None
    workers: list[dict[str, Any]] = Field(default_factory=list)
    worker_online: bool = False
    required_queues_ready: bool = False


class AppSessionUser(BaseModel):
    """User identity in the single app-session snapshot."""

    id: str
    email: str
    first_name: str | None = None
    display_name: str | None = None


class AppSessionSyncState(BaseModel):
    """Import/enrichment status in the single app-session snapshot."""

    last_sync_at: str | None = None
    last_error: str | None = None
    enrichment_pending_count: int = 0
    ready_group_count: int = 0
    oldest_imported_at: str | None = None
    full_import_running: bool = False
    full_import_completed: bool = False
    full_import_completed_at: str | None = None
    pending_action_count: int = 0
    last_action_sync_at: str | None = None
    last_action_error: str | None = None
    last_ai_error: str | None = None


class AppSessionResponse(BaseModel):
    """Single product-state snapshot consumed by post-login, dashboard, and Gmail."""

    user: AppSessionUser
    readiness: PostLoginReadinessResponse
    dashboard: DashboardResponse
    mailbox: MailboxResponse
    sync: AppSessionSyncState

MailGroupRow = GmailThreadRow
MailGroupSection = GmailThreadSection
MailGroupListResponse = MailboxResponse
MailGroupMessage = ThreadMessage
MailGroupDetailResponse = ThreadReaderResponse
DashboardMailGroupItem = AttentionItem
