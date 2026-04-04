import type { Entity, PipelineOutput, TimingBand } from '@electronic-mail/types';

const HOURS_24_IN_MS = 24 * 60 * 60 * 1000;
const DAYS_3_IN_MS = 3 * HOURS_24_IN_MS;

export function assignTiming(
  entity: Entity,
  output: PipelineOutput,
  currentTime: string,
): PipelineOutput {
  if (output.attention_item === null) {
    return output;
  }

  const timingBand = deriveTimingBand(entity, output, output.attention_item, currentTime);

  return {
    ...output,
    attention_item: {
      ...output.attention_item,
      timing_band: timingBand,
      importance_level: deriveImportanceLevel(entity, timingBand),
    },
  };
}

function deriveTimingBand(
  entity: Entity,
  output: PipelineOutput,
  attentionItem: NonNullable<PipelineOutput['attention_item']>,
  currentTime: string,
): TimingBand {
  if (output.suppressed) {
    return 'hidden';
  }

  if (entity.lifecycle_state === 'resolved') {
    return 'hidden';
  }

  if (entity.due_at !== null) {
    const dueAtMs = Date.parse(entity.due_at);
    const referenceMs = Date.parse(currentTime);

    if (!Number.isNaN(dueAtMs) && !Number.isNaN(referenceMs)) {
      const deltaMs = dueAtMs - referenceMs;

      if (deltaMs < HOURS_24_IN_MS) {
        return 'now';
      }

      if (deltaMs <= DAYS_3_IN_MS) {
        return 'today';
      }

      return 'later';
    }
  }

  if (attentionItem.need_type === 'decision' && entity.importance) {
    return 'today';
  }

  return 'later';
}

function deriveImportanceLevel(
  entity: Entity,
  timingBand: TimingBand,
): NonNullable<NonNullable<PipelineOutput['attention_item']>['importance_level']> {
  if (timingBand === 'now') {
    return 'high';
  }

  if (entity.importance || timingBand === 'today') {
    return 'medium';
  }

  return 'low';
}
