/** Web-local aliases over the shared feed contract package. */
import type {
  AttentionItem,
  DashboardBriefing,
  DashboardResponse,
  DashboardProfile,
  FeedResponse,
  GoogleAuthState,
  HistoryDayGroup,
  HistoryItem,
  HistoryMonthGroup,
  HistoryResponse,
  ThreadMessage,
  ThreadReaderResponse,
  HistoryYearGroup,
  TraceReplayResponse,
  TimingBand,
} from '@decision-pipeline/types';

export type FeedItem = AttentionItem;
export type PrimaryActionType = AttentionItem['primary_action'];

export type {
  DashboardBriefing,
  DashboardProfile,
  DashboardResponse,
  FeedResponse,
  GoogleAuthState,
  HistoryDayGroup,
  HistoryItem,
  HistoryMonthGroup,
  HistoryResponse,
  ThreadMessage,
  ThreadReaderResponse,
  HistoryYearGroup,
  TraceReplayResponse,
  TimingBand,
};
