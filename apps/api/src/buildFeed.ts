import type { AttentionItem, FeedResponse, LifecycleState, PipelineOutput } from '@decision-pipeline/types';

type FeedSection = keyof FeedResponse;

type FeedCandidate = {
  readonly output: PipelineOutput;
  readonly attentionItem: AttentionItem;
  readonly section: FeedSection;
};

const SECTION_PRIORITY: Record<FeedSection, number> = {
  now: 0,
  today: 1,
  worth_knowing: 2,
};

const TIMING_SCORE: Record<AttentionItem['timing_band'], number> = {
  now: 400,
  today: 250,
  later: 100,
  hidden: -1000,
};

const IMPORTANCE_SCORE: Record<NonNullable<AttentionItem['importance_level']>, number> = {
  high: 90,
  medium: 50,
  low: 10,
};

const CONFIDENCE_SCORE: Record<AttentionItem['action_confidence'], number> = {
  high: 30,
  medium: 18,
  low: 0,
};

const EFFORT_SCORE: Record<AttentionItem['effort_level'], number> = {
  quick: 8,
  deep: 2,
};

const LIFECYCLE_SCORE: Record<NonNullable<AttentionItem['lifecycle_state']>, number> = {
  active: 12,
  scheduled: 8,
  resolved: -1000,
  suppressed: -1000,
};

const BLOCKING_STATES = new Set(['awaiting_reply', 'awaiting_rsvp', 'awaiting_payment']);

const ENABLE_INVARIANT_ASSERTIONS = process.env.NODE_ENV !== 'production';

/**
 * DESIGN RULE (CRITICAL)
 *
 * Feed is a PURE projection of pipeline output.
 *
 * Feed MUST:
 * - map
 * - filter
 * - sort
 *
 * Feed MUST NOT:
 * - infer
 * - override
 * - reinterpret decision logic
 *
 * PipelineOutput is the single source of truth.
 */
export function buildFeed(outputs: PipelineOutput[]): FeedResponse {
  const selectedByEntityId = new Map<string, FeedCandidate>();

  for (const output of outputs) {
    const candidate = toFeedCandidate(output);

    if (candidate === null) {
      continue;
    }

    const existing = selectedByEntityId.get(candidate.attentionItem.entity_id);

    if (existing === undefined || shouldReplaceCandidate(existing, candidate)) {
      selectedByEntityId.set(candidate.attentionItem.entity_id, candidate);
    }
  }

  const feed: FeedResponse = {
    now: [],
    today: [],
    worth_knowing: [],
  };

  for (const candidate of selectedByEntityId.values()) {
    const feedItem = enrichAttentionItem(candidate);

    assertPlacement(feedItem, candidate.section);
    feed[candidate.section].push(feedItem);
  }

  feed.now.sort((left, right) =>
    compareBySection(selectedByEntityId.get(left.entity_id)!, selectedByEntityId.get(right.entity_id)!),
  );
  feed.today.sort((left, right) =>
    compareBySection(selectedByEntityId.get(left.entity_id)!, selectedByEntityId.get(right.entity_id)!),
  );
  feed.worth_knowing.sort((left, right) =>
    compareBySection(selectedByEntityId.get(left.entity_id)!, selectedByEntityId.get(right.entity_id)!),
  );

  return feed;
}

function toFeedCandidate(output: PipelineOutput): FeedCandidate | null {
  if (output.suppressed || output.entity.lifecycle_state === 'resolved') {
    return null;
  }

  if (output.attention_item === null || output.attention_item.timing_band === 'hidden') {
    return null;
  }

  return {
    output,
    attentionItem: output.attention_item,
    section: mapTimingBandToSection(output.attention_item.timing_band),
  };
}

function mapTimingBandToSection(
  timingBand: NonNullable<PipelineOutput['attention_item']>['timing_band'],
): FeedSection {
  if (timingBand === 'now') {
    return 'now';
  }

  if (timingBand === 'today') {
    return 'today';
  }

  return 'worth_knowing';
}

function shouldReplaceCandidate(existing: FeedCandidate, incoming: FeedCandidate): boolean {
  const priorityDelta = SECTION_PRIORITY[incoming.section] - SECTION_PRIORITY[existing.section];

  if (priorityDelta !== 0) {
    return priorityDelta < 0;
  }

  return compareBySection(incoming, existing) < 0;
}

function compareBySection(left: FeedCandidate, right: FeedCandidate): number {
  const scoreDelta = getCandidateScore(right) - getCandidateScore(left);

  if (scoreDelta !== 0) {
    return scoreDelta;
  }

  const dueComparison = compareByDueAt(left, right);

  if (dueComparison !== 0) {
    return dueComparison;
  }

  return left.attentionItem.entity_id.localeCompare(right.attentionItem.entity_id);
}

function getCandidateScore(candidate: FeedCandidate): number {
  const importanceLevel =
    candidate.attentionItem.importance_level ?? (candidate.output.entity.importance ? 'medium' : 'low');
  const lifecycleState = normalizeLifecycleState(
    candidate.attentionItem.lifecycle_state ?? candidate.output.entity.lifecycle_state,
  );
  const currentState = candidate.attentionItem.current_state ?? candidate.output.entity.current_state;
  const blockingBoost = BLOCKING_STATES.has(currentState) ? 20 : 0;

  return (
    TIMING_SCORE[candidate.attentionItem.timing_band] +
    IMPORTANCE_SCORE[importanceLevel] +
    CONFIDENCE_SCORE[candidate.attentionItem.action_confidence] +
    EFFORT_SCORE[candidate.attentionItem.effort_level] +
    LIFECYCLE_SCORE[lifecycleState] +
    blockingBoost
  );
}

function enrichAttentionItem(candidate: FeedCandidate): AttentionItem {
  return {
    ...candidate.attentionItem,
    ...(candidate.output.entity.due_at !== null ? { due_at: candidate.output.entity.due_at } : {}),
    ...(candidate.output.entity.source !== undefined ? { source: candidate.output.entity.source } : {}),
    ...(candidate.attentionItem.lifecycle_state !== undefined
      ? {}
      : { lifecycle_state: candidate.output.entity.lifecycle_state }),
    ...(candidate.attentionItem.current_state !== undefined
      ? {}
      : { current_state: candidate.output.entity.current_state }),
  };
}

function normalizeLifecycleState(value: string): LifecycleState {
  if (value === 'scheduled') {
    return 'scheduled';
  }

  if (value === 'resolved') {
    return 'resolved';
  }

  if (value === 'suppressed') {
    return 'suppressed';
  }

  return 'active';
}

function compareByDueAt(left: FeedCandidate, right: FeedCandidate): number {
  const leftDueAt = toDueAtTimestamp(left.output.entity.due_at);
  const rightDueAt = toDueAtTimestamp(right.output.entity.due_at);

  if (leftDueAt !== null && rightDueAt !== null) {
    if (leftDueAt !== rightDueAt) {
      return leftDueAt - rightDueAt;
    }
  } else if (leftDueAt !== null) {
    return -1;
  } else if (rightDueAt !== null) {
    return 1;
  }

  return left.attentionItem.entity_id.localeCompare(right.attentionItem.entity_id);
}

function toDueAtTimestamp(dueAt: string | null): number | null {
  if (dueAt === null) {
    return null;
  }

  const timestamp = Date.parse(dueAt);

  return Number.isNaN(timestamp) ? null : timestamp;
}

function assertPlacement(item: AttentionItem, section: FeedSection): void {
  if (!ENABLE_INVARIANT_ASSERTIONS) {
    return;
  }

  if (section === 'now' && item.timing_band !== 'now') {
    throw new Error(
      `Invariant violation: entity ${item.entity_id} expected now but got ${item.timing_band}`,
    );
  }

  if (section === 'today' && item.timing_band !== 'today') {
    throw new Error(
      `Invariant violation: entity ${item.entity_id} expected today but got ${item.timing_band}`,
    );
  }

  if (section === 'worth_knowing' && item.timing_band !== 'later') {
    throw new Error(
      `Invariant violation: entity ${item.entity_id} expected later but got ${item.timing_band}`,
    );
  }
}
