import type { AttentionItem, FeedResponse, PipelineOutput } from '@decision-pipeline/types';

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
    assertPlacement(candidate.attentionItem, candidate.section);
    feed[candidate.section].push(candidate.attentionItem);
  }

  feed.now.sort((left, right) =>
    compareBySection(
      selectedByEntityId.get(left.entity_id)!,
      selectedByEntityId.get(right.entity_id)!,
      'now',
    ),
  );
  feed.today.sort((left, right) =>
    compareBySection(
      selectedByEntityId.get(left.entity_id)!,
      selectedByEntityId.get(right.entity_id)!,
      'today',
    ),
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

  return compareBySection(incoming, existing, incoming.section) < 0;
}

function compareBySection(
  left: FeedCandidate,
  right: FeedCandidate,
  section: FeedSection,
): number {
  if (section === 'now') {
    return compareByDueAt(left, right);
  }

  if (section === 'today') {
    const dueComparison = compareByDueAt(left, right);

    if (dueComparison !== 0) {
      return dueComparison;
    }

    const importanceComparison =
      Number(right.output.entity.importance) - Number(left.output.entity.importance);

    if (importanceComparison !== 0) {
      return importanceComparison;
    }
  }

  return left.attentionItem.entity_id.localeCompare(right.attentionItem.entity_id);
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
