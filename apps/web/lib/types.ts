export type TimingBand = 'now' | 'today' | 'later' | 'hidden';
export type NeedType = 'decision' | 'awareness';
export type ActionType = 'inline' | 'external' | 'none';
export type EffortLevel = 'quick' | 'deep';
export type ActionConfidence = 'high' | 'medium' | 'low';
export type PrimaryActionType =
  | 'reply'
  | 'open'
  | 'confirm'
  | 'pay'
  | 'track'
  | 'review'
  | 'join'
  | 'send'
  | 'approve'
  | 'register'
  | 'none';
export type ImportanceLevel = 'high' | 'medium' | 'low';
export type SourceType = 'gmail' | 'calendar';

export interface FeedItem {
  id: string;
  entity_id: string;
  user_id: string;
  need_type: NeedType;
  action_type: ActionType;
  effort_level: EffortLevel;
  timing_band: TimingBand;
  action_confidence: ActionConfidence;
  primary_action: PrimaryActionType;
  fallback_action: 'open' | 'none';
  title: string;
  why_this_is_here: string;
  due_at?: string | null;
  importance_level?: ImportanceLevel;
  lifecycle_state?: string;
  current_state?: string;
  source?: SourceType;
  trace_id: string;
  created_at: string;
}

export interface FeedResponse {
  now: FeedItem[];
  today: FeedItem[];
  worth_knowing: FeedItem[];
}
