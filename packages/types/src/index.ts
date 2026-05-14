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
export type DashboardImportJobStatus = 'queued' | 'running' | 'succeeded' | 'failed';

export type FeedbackType = 'acted' | 'ignored' | 'snoozed' | 'dismissed';

export type TraceStage =
  | 'ingestion'
  | 'normalization'
  | 'grouping'
  | 'state_derivation'
  | 'decision'
  | 'action_selection'
  | 'timing'
  | 'ranking'
  | 'output'
  | 'lifecycle_transition';

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
}

export interface DashboardResponse {
  readonly auth: GoogleAuthState;
  readonly profile?: DashboardProfile | null;
  readonly briefing?: DashboardBriefing | null;
  readonly feed: FeedResponse;
  readonly runtime_status?: Record<string, unknown>;
}

export interface DashboardImportJobResponse {
  readonly id: string;
  readonly user_id: string;
  readonly status: DashboardImportJobStatus;
  readonly stage: string;
  readonly imported_count: number;
  readonly total_count?: number | null;
  readonly source_records: number;
  readonly changed_entities: number;
  readonly refreshed_entities: number;
  readonly result_status?: string | null;
  readonly error_message?: string | null;
  readonly created_at: string;
  readonly started_at?: string | null;
  readonly stage_started_at?: string | null;
  readonly stage_durations?: Record<string, number>;
  readonly completed_at?: string | null;
  readonly updated_at: string;
}

export interface FirstRunImportJobResponse {
  readonly id: string;
  readonly user_id: string;
  readonly status: DashboardImportJobStatus;
  readonly stage: string;
  readonly fetched_count: number;
  readonly total_count?: number | null;
  readonly thread_count: number;
  readonly dashboard_item_count: number;
  readonly inbox_ready_at?: string | null;
  readonly first_groups_ready_at?: string | null;
  readonly dashboard_ready_at?: string | null;
  readonly fast_dashboard_ready_at?: string | null;
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
  readonly latest_source_record_id: string;
  readonly latest_received_at: string;
  readonly latest_subject?: string | null;
  readonly latest_sender?: string | null;
  readonly participants: string[];
  readonly message_count: number;
  readonly summary?: string | null;
  readonly snippet?: string | null;
  readonly label_ids?: string[];
  readonly unread?: boolean;
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
  readonly status: 'queued' | 'not_connected';
  readonly state: MailboxSyncStateResponse;
  readonly job_id?: string | null;
  readonly queued_at?: string | null;
}

/**
 * Debug trace for each pipeline stage
 */
export interface TraceRecord {
  readonly id: string;
  readonly trace_id: string;
  readonly entity_id: string | null;
  readonly source_record_id?: string | null;
  readonly user_id: string;

  readonly stage: TraceStage;

  readonly input: Record<string, unknown>;
  readonly output: Record<string, unknown>;

  readonly created_at: string;
}

export interface TraceReplayResponse {
  readonly entity_id: string;
  readonly source_record_ids: string[];
  readonly items: TraceRecord[];
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

export interface EntityOutcomeResponse {
  readonly id: string;
  readonly user_id: string;
  readonly entity_id: string;
  readonly outcome_type: 'complete' | 'snooze' | 'dismiss';
  readonly snooze_until?: string | null;
  readonly note?: string | null;
  readonly created_at: string;
}

export interface GmailDraftRequest {
  readonly to: string;
  readonly cc?: string | null;
  readonly bcc?: string | null;
  readonly subject: string;
  readonly body: string;
  readonly entity_id?: string | null;
  readonly thread_id?: string | null;
}

export interface GmailDraftResponse extends GmailDraftRequest {
  readonly id: string;
  readonly user_id: string;
  readonly gmail_draft_id: string;
  readonly gmail_message_id?: string | null;
  readonly status: 'draft' | 'sent' | 'deleted';
  readonly created_at: string;
  readonly updated_at: string;
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

/**
 * User feedback signal for learning/replay
 */
export interface FeedbackEvent {
  readonly id: string;
  readonly entity_id: string;
  readonly user_id: string;

  readonly type: FeedbackType;

  readonly metadata: Record<string, unknown>;

  readonly created_at: string;
}

export type MailGroupRow = GmailThreadRow;
export type MailGroupSection = GmailThreadSection;
export type MailGroupListResponse = MailboxResponse;
export type MailGroupMessage = ThreadMessage;
export type MailGroupDetailResponse = ThreadReaderResponse;
export type DashboardMailGroupItem = AttentionItem;

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
