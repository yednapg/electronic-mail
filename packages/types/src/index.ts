/** Shared TypeScript contracts used by the frontend and fixtures. */
export type NeedType = 'decision' | 'awareness';

export type ActionType = 'inline' | 'external' | 'none';

export type EffortLevel = 'quick' | 'deep';

export type TimingBand = 'now' | 'today' | 'later' | 'hidden';

export type ActionConfidence = 'high' | 'medium' | 'low';

export type LifecycleState = 'active' | 'scheduled' | 'resolved' | 'suppressed';
export type EntityCurrentState = 'open' | 'waiting' | 'done';

export type SourceType = 'gmail' | 'calendar' | 'manual';
export type GmailThreadAction = 'archive' | 'unarchive' | 'mark_read';
export type MailboxLabel = 'inbox' | 'sent' | 'drafts' | 'trash' | 'archive' | 'all';
export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed';

type EntityThreadIdentity = {
  readonly thread_id: string;
  readonly group_id?: never;
};

type EntityGroupIdentity = {
  readonly thread_id?: never;
  readonly group_id: string;
};

/**
 * Raw ingested record from external source (Gmail / Calendar)
 */
export interface SourceRecord {
  readonly id: string;
  readonly user_id: string;
  readonly source: SourceType;
  readonly thread_id: string;
  readonly raw_payload: Record<string, unknown>;
  readonly received_at: string; // ISO timestamp
}

/**
 * Normalized entity representing a real-world unit (thread, event, bill, etc.)
 */
export type Entity = (EntityThreadIdentity | EntityGroupIdentity) & {
  readonly id: string; // entity_id
  readonly user_id: string;
  readonly source?: SourceType;

  readonly current_state: EntityCurrentState;
  readonly due_at: string | null;

  readonly importance: boolean;

  readonly lifecycle_state: LifecycleState;

  readonly created_at: string;
  readonly updated_at: string;
};

/**
 * User-facing actionable item derived from Entity
 */
export interface AttentionItemDetail {
  readonly body: readonly string[];
  readonly action_label: string;
  readonly source_label: string;
  readonly action_url?: string | null;
}

export interface AttentionItem {
  readonly id: string;
  readonly entity_id: string;
  readonly user_id: string;

  readonly need_type: NeedType;
  readonly action_type: ActionType;
  readonly effort_level: EffortLevel;

  readonly timing_band: TimingBand;
  readonly action_confidence: ActionConfidence;

  readonly primary_action: string;
  readonly fallback_action: 'open';

  readonly title: string;
  readonly why_this_is_here: string;
  readonly detail?: AttentionItemDetail | null;
  readonly due_at?: string | null;
  readonly importance_level?: 'high' | 'medium' | 'low';
  readonly lifecycle_state?: string;
  readonly current_state?: EntityCurrentState;
  readonly source?: SourceType;
  readonly gmail_thread_id?: string | null;
  readonly gmail_thread_action?: GmailThreadAction | null;

  readonly trace_id: string;

  readonly created_at: string;
}

/**
 * Final output of the pipeline per entity
 */
export interface PipelineOutput {
  readonly entity: Entity;

  // null if suppressed
  readonly attention_item: AttentionItem | null;

  readonly suppressed: boolean;
  readonly suppression_reason: string | null;
}

export interface FeedResponse {
  readonly now: AttentionItem[];
  readonly today: AttentionItem[];
  readonly worth_knowing: AttentionItem[];
}

export interface GoogleAuthState {
  readonly available: boolean;
  readonly connected: boolean;
  readonly connect_url?: string | null;
}

export interface AuthUserResponse {
  readonly id: string;
  readonly email: string;
  readonly display_name?: string | null;
  readonly access_enabled: boolean;
}

export interface AuthMeResponse {
  readonly authenticated: boolean;
  readonly user?: AuthUserResponse | null;
}

export interface MobileSessionExchangeRequest {
  readonly login_code: string;
}

export interface MobileSessionExchangeResponse {
  readonly session_token: string;
  readonly expires_at: string;
  readonly user: AuthUserResponse;
}

export interface DashboardProfile {
  readonly email?: string | null;
  readonly display_name?: string | null;
}

export interface DashboardBriefing {
  readonly headline: string;
  readonly brief: string;
  readonly parts?: readonly DashboardBriefingPart[];
  readonly important?: DashboardBriefingImportant | null;
  readonly calendar_availability?: DashboardCalendarAvailability | null;
}

export interface DashboardBriefingPart {
  readonly type: 'meetings' | 'tasks' | 'emails' | string;
  readonly emoji: string;
  readonly count: number;
  readonly text: string;
}

export interface DashboardBriefingImportant {
  readonly emoji: string;
  readonly count: number;
  readonly text: string;
  readonly mail_group_id?: string | null;
  readonly action_type?: string | null;
}

export interface DashboardCalendarAvailability {
  readonly emoji: string;
  readonly kind: string;
  readonly time?: string | null;
  readonly text: string;
}

export interface DashboardResponse {
  readonly auth: GoogleAuthState;
  readonly profile?: DashboardProfile | null;
  readonly briefing?: DashboardBriefing | null;
  readonly feed: FeedResponse;
  readonly runtime_status?: Record<string, unknown>;
}


export interface FirstRunImportJobResponse {
  readonly id: string;
  readonly user_id: string;
  readonly status: JobStatus;
  readonly stage: string;
  readonly fetched_count: number;
  readonly total_count?: number | null;
  readonly thread_count: number;
  readonly dashboard_item_count: number;
  readonly inbox_ready_at?: string | null;
  readonly first_groups_ready_at?: string | null;
  readonly dashboard_ready_at?: string | null;
  readonly canonical_dashboard_ready_at?: string | null;
  readonly quality_status?: 'pending' | 'ready' | 'failed';
  readonly quality_error?: string | null;
  readonly full_import_started_at?: string | null;
  readonly full_import_completed_at?: string | null;
  readonly error_message?: string | null;
  readonly created_at: string;
  readonly started_at?: string | null;
  readonly completed_at?: string | null;
  readonly updated_at: string;
}

export interface PostLoginReadinessResponse {
  readonly mode: 'returning' | 'first_time';
  readonly stage:
    | 'welcome_back'
    | 'starting_full_import'
    | 'importing_recent_gmail'
    | 'grouping_threads'
    | 'writing_titles'
    | 'building_dashboard'
    | 'ready'
    | 'failed';
  readonly ready_to_enter: boolean;
  readonly dashboard_ready: boolean;
  readonly mailbox_ready: boolean;
  readonly ready_dashboard_count: number;
  readonly ready_mail_group_count: number;
  readonly full_import_running: boolean;
  readonly full_import_completed: boolean;
  readonly user_display_name?: string | null;
  readonly error_message?: string | null;
}

export interface HistoryItem {
  readonly source_record_id: string;
  readonly entity_id?: string | null;
  readonly source: SourceType;
  readonly thread_id?: string | null;
  readonly received_at: string;
  readonly subject?: string | null;
  readonly title?: string | null;
  readonly sender?: string | null;
  readonly snippet?: string | null;
  readonly summary?: string | null;
  readonly current_state?: EntityCurrentState | null;
  readonly lifecycle_state?: LifecycleState | null;
  readonly outcome_type?: 'complete' | 'snooze' | 'dismiss' | null;
  readonly outcome_created_at?: string | null;
}

export interface HistoryDayGroup {
  readonly date: string;
  readonly rows: HistoryItem[];
}

export interface HistoryMonthGroup {
  readonly month: string;
  readonly days: HistoryDayGroup[];
}

export interface HistoryYearGroup {
  readonly year: string;
  readonly months: HistoryMonthGroup[];
}

export interface HistoryResponse {
  readonly limit: number;
  readonly offset: number;
  readonly total: number;
  readonly years: HistoryYearGroup[];
}

export interface GmailThreadRow {
  readonly thread_id: string;
  readonly entity_id?: string | null;
  readonly title?: string | null;
  readonly href?: string | null;
  readonly latest_source_record_id: string;
  readonly latest_received_at: string;
  readonly latest_message_at?: string | null;
  readonly latest_subject?: string | null;
  readonly latest_sender?: string | null;
  readonly sender?: string | null;
  readonly participants: string[];
  readonly message_count: number;
  readonly summary?: string | null;
  readonly snippet?: string | null;
  readonly label_ids?: string[];
  readonly labels?: string[];
  readonly unread?: boolean;
  readonly action_needed?: boolean;
  readonly action_type?: 'pay' | 'reply' | 'confirm' | 'track' | 'review' | 'read' | 'open' | 'none';
  readonly action_type_key?: 'pay' | 'reply' | 'confirm' | 'track' | 'review' | 'read' | 'open' | 'none';
  readonly priority?: number;
  readonly dashboard_visible?: boolean;
  readonly current_state?: EntityCurrentState | null;
  readonly lifecycle_state?: LifecycleState | null;
  readonly outcome_type?: 'complete' | 'snooze' | 'dismiss' | null;
  readonly lifecycle_updates: GmailThreadUpdate[];
  readonly enrichment_status?: 'pending' | 'ready' | 'failed';
}

export interface GmailThreadUpdate {
  readonly source_record_id: string;
  readonly received_at: string;
  readonly subject?: string | null;
  readonly sender?: string | null;
  readonly summary?: string | null;
}

export interface GmailThreadMutationResponse {
  readonly thread_id: string;
  readonly action: GmailThreadAction;
}

export interface TaskCreateRequest {
  readonly title: string;
  readonly notes?: string | null;
  readonly section?: 'now' | 'today' | 'later';
  readonly due_at?: string | null;
}

export interface TaskUpdateRequest {
  readonly title?: string | null;
  readonly notes?: string | null;
  readonly section?: 'now' | 'today' | 'later' | null;
  readonly due_at?: string | null;
  readonly status?: 'open' | 'done' | null;
}

export interface TaskResponse {
  readonly id: string;
  readonly user_id: string;
  readonly entity_id: string;
  readonly title: string;
  readonly notes?: string | null;
  readonly section: 'now' | 'today' | 'later';
  readonly due_at?: string | null;
  readonly status: 'open' | 'done';
  readonly created_at: string;
  readonly updated_at: string;
}

export interface EntityOutcomeRequest {
  readonly note?: string | null;
}

export interface EntitySnoozeRequest extends EntityOutcomeRequest {
  readonly snooze_until: string;
}

export interface EntityOutcomeResponse {
  readonly id: string;
  readonly user_id: string;
  readonly entity_id: string;
  readonly outcome_type: 'complete' | 'snooze' | 'dismiss';
  readonly snooze_until?: string | null;
  readonly note?: string | null;
  readonly created_at: string;
}

export interface GmailThreadSection {
  readonly id: string;
  readonly title: string;
  readonly rows: GmailThreadRow[];
}

export interface GmailViewResponse {
  readonly total_threads: number;
  readonly sections: GmailThreadSection[];
}

export interface MailboxResponse {
  readonly label: MailboxLabel;
  readonly total_threads: number;
  readonly next_cursor?: string | null;
  readonly sections: GmailThreadSection[];
  readonly ready_count?: number;
  readonly pending_count?: number;
  readonly oldest_imported_at?: string | null;
  readonly full_import_running?: boolean;
  readonly full_import_completed?: boolean;
}

export interface MailboxSyncStateResponse {
  readonly connected: boolean;
  readonly last_history_id?: string | null;
  readonly last_full_sync_at?: string | null;
  readonly watch_expiration_at?: string | null;
  readonly last_sync_started_at?: string | null;
  readonly last_sync_completed_at?: string | null;
  readonly last_sync_error?: string | null;
  readonly total_threads: number;
}

export interface MailboxSyncTriggerResponse {
  readonly status: 'queued' | 'synced' | 'not_connected';
  readonly state: MailboxSyncStateResponse;
  readonly job_id?: string | null;
  readonly queued_at?: string | null;
}

export type ThreadMessageReaderMarkerKind = 'external_warning' | 'classification';

export interface ThreadMessageReaderMarker {
  readonly kind: ThreadMessageReaderMarkerKind;
  readonly label: string;
  readonly text: string;
}

export interface ThreadMessageReader {
  readonly primary_text: string;
  readonly markers: ThreadMessageReaderMarker[];
  readonly signature_text?: string | null;
  readonly quoted_text?: string | null;
  readonly footer_text?: string | null;
  readonly original_html_available: boolean;
}

export interface ThreadMessage {
  readonly id: string;
  readonly source: SourceType;
  readonly thread_id?: string | null;
  readonly from_address?: string | null;
  readonly to?: string | null;
  readonly cc?: string | null;
  readonly bcc?: string | null;
  readonly subject?: string | null;
  readonly body: string;
  readonly html_body?: string | null;
  readonly html_render_document?: string | null;
  readonly reader?: ThreadMessageReader | null;
  readonly snippet?: string | null;
  readonly label_ids: string[];
  readonly received_at: string;
}

export interface ThreadReaderResponse {
  readonly entity_id: string;
  readonly user_id: string;
  readonly source?: SourceType | null;
  readonly gmail_thread_id?: string | null;
  readonly subject?: string | null;
  readonly total_messages: number;
  readonly limit: number;
  readonly offset: number;
  readonly has_more: boolean;
  readonly messages: ThreadMessage[];
}


export type MailGroupRow = GmailThreadRow;
export type MailGroupSection = GmailThreadSection;
export type MailGroupListResponse = MailboxResponse;
export type MailGroupMessage = ThreadMessage;
export type MailGroupDetailResponse = ThreadReaderResponse;
export type DashboardMailGroupItem = AttentionItem;

export interface AppSessionUser {
  readonly id: string;
  readonly email: string;
  readonly first_name?: string | null;
  readonly display_name?: string | null;
}

export interface AppSessionSyncState {
  readonly last_sync_at?: string | null;
  readonly last_error?: string | null;
  readonly enrichment_pending_count: number;
  readonly ready_group_count: number;
  readonly oldest_imported_at?: string | null;
  readonly full_import_running: boolean;
  readonly full_import_completed: boolean;
}

export interface AppSessionStateResponse {
  readonly user: AppSessionUser;
  readonly readiness: PostLoginReadinessResponse;
  readonly dashboard: DashboardResponse;
  readonly mailbox: MailboxResponse;
  readonly sync: AppSessionSyncState;
}

export interface BackgroundJobResponse {
  readonly id: string;
  readonly kind: string;
  readonly status: string;
  readonly stage?: string | null;
  readonly attempt_count: number;
  readonly last_error?: string | null;
  readonly created_at: string;
  readonly started_at?: string | null;
  readonly completed_at?: string | null;
  readonly updated_at: string;
}

export interface OpsHealthResponse {
  readonly queue_depth: Record<string, number>;
  readonly dead_jobs: number;
  readonly stale_running_jobs: number;
  readonly oldest_queued_age_seconds?: number | null;
  readonly workers: Array<Record<string, unknown>>;
}
