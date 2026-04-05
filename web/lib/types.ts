/** Web-local aliases over the shared feed contract package. */
import type {
  AttentionItem,
  FeedResponse,
  TimingBand,
} from '@decision-pipeline/types';

export type FeedItem = AttentionItem;
export type PrimaryActionType = AttentionItem['primary_action'];

export type {
  FeedResponse,
  TimingBand,
};
