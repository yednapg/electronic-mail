export type NeedType = 'decision' | 'awareness';

export type ActionType = 'inline' | 'external' | 'none';

export type EffortLevel = 'quick' | 'deep';

export type TimingBand = 'now' | 'today' | 'later' | 'hidden';

export type ActionConfidence = 'high' | 'medium' | 'low';

export type LifecycleState = 'active' | 'scheduled' | 'resolved' | 'suppressed';

export type SourceType = 'gmail' | 'calendar';

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
  | 'output';

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

  readonly current_state: string;
  readonly due_at?: string | null;

  readonly importance: boolean;

  readonly lifecycle_state: LifecycleState;

  readonly created_at: string;
  readonly updated_at: string;
};

/**
 * User-facing actionable item derived from Entity
 */
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

/**
 * Debug trace for each pipeline stage
 */
export interface TraceRecord {
  readonly id: string;
  readonly entity_id: string;
  readonly user_id: string;

  readonly stage: TraceStage;

  readonly input: Record<string, unknown>;
  readonly output: Record<string, unknown>;

  readonly created_at: string;
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
