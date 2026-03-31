import test from 'node:test';
import assert from 'node:assert/strict';

import type { AttentionItem, Entity, PipelineOutput } from '@decision-pipeline/types';

import { assignTiming } from './assignTiming';

const CURRENT_TIME = '2026-03-31T00:00:00.000Z';

type EntityOverrides = Partial<Omit<Entity, 'thread_id' | 'group_id'>>;

function createEntity(overrides: EntityOverrides = {}): Entity {
  return {
    id: 'entity-1',
    user_id: 'user-1',
    thread_id: 'thread-1',
    current_state: 'awaiting_reply',
    due_at: null,
    importance: true,
    lifecycle_state: 'active',
    created_at: '2026-03-31T00:00:00.000Z',
    updated_at: '2026-03-31T00:00:00.000Z',
    ...overrides,
  } satisfies Entity;
}

function createAttentionItem(overrides: Partial<AttentionItem> = {}): AttentionItem {
  return {
    id: 'entity-1',
    entity_id: 'entity-1',
    user_id: 'user-1',
    need_type: 'decision',
    action_type: 'inline',
    effort_level: 'quick',
    timing_band: 'today',
    action_confidence: 'high',
    primary_action: 'reply',
    fallback_action: 'open',
    title: 'Reply needed',
    why_this_is_here: 'This is here because the thread appears to need a reply.',
    trace_id: 'entity-1',
    created_at: '2026-03-31T00:00:00.000Z',
    ...overrides,
  };
}

function createOutput(overrides: Partial<PipelineOutput> = {}): PipelineOutput {
  const entity = createEntity();

  return {
    entity,
    attention_item: createAttentionItem(),
    suppressed: false,
    suppression_reason: null,
    ...overrides,
  };
}

test('due within 24h becomes now', () => {
  const entity = createEntity({
    due_at: '2026-03-31T12:00:00.000Z',
  });
  const output = createOutput({ entity });

  const result = assignTiming(entity, output, CURRENT_TIME);

  assert.equal(result.attention_item?.timing_band, 'now');
});

test('due in 2 days becomes today', () => {
  const entity = createEntity({
    due_at: '2026-04-02T00:00:00.000Z',
  });
  const output = createOutput({ entity });

  const result = assignTiming(entity, output, CURRENT_TIME);

  assert.equal(result.attention_item?.timing_band, 'today');
});

test('due in 5 days becomes later', () => {
  const entity = createEntity({
    due_at: '2026-04-05T00:00:00.000Z',
  });
  const output = createOutput({ entity });

  const result = assignTiming(entity, output, CURRENT_TIME);

  assert.equal(result.attention_item?.timing_band, 'later');
});

test('no due plus important becomes today', () => {
  const entity = createEntity({
    due_at: null,
    importance: true,
  });
  const output = createOutput({
    entity,
    attention_item: createAttentionItem({
      need_type: 'decision',
      action_type: 'external',
      primary_action: 'open',
    }),
  });

  const result = assignTiming(entity, output, CURRENT_TIME);

  assert.equal(result.attention_item?.timing_band, 'today');
});

test('no due plus not important becomes later', () => {
  const entity = createEntity({
    due_at: null,
    importance: false,
  });
  const output = createOutput({
    entity,
    attention_item: createAttentionItem({
      need_type: 'decision',
      action_type: 'external',
      action_confidence: 'low',
      primary_action: 'open',
    }),
  });

  const result = assignTiming(entity, output, CURRENT_TIME);

  assert.equal(result.attention_item?.timing_band, 'later');
});

test('suppressed output becomes hidden', () => {
  const entity = createEntity();
  const output = createOutput({
    entity,
    suppressed: true,
    suppression_reason: 'no_action_needed',
  });

  const result = assignTiming(entity, output, CURRENT_TIME);

  assert.equal(result.attention_item?.timing_band, 'hidden');
});

test('resolved entity becomes hidden', () => {
  const entity = createEntity({
    lifecycle_state: 'resolved',
  });
  const output = createOutput({ entity });

  const result = assignTiming(entity, output, CURRENT_TIME);

  assert.equal(result.attention_item?.timing_band, 'hidden');
});

test('invalid due_at is treated as no due_at', () => {
  const entity = createEntity({
    due_at: 'not-a-date',
    importance: true,
  });
  const output = createOutput({
    entity,
    attention_item: createAttentionItem({
      need_type: 'decision',
      action_type: 'external',
      primary_action: 'open',
    }),
  });

  const result = assignTiming(entity, output, CURRENT_TIME);

  assert.equal(result.attention_item?.timing_band, 'today');
});

test('timing output is deterministic for the same input and current time', () => {
  const entity = createEntity({
    due_at: '2026-04-02T00:00:00.000Z',
  });
  const output = createOutput({ entity });

  const first = assignTiming(entity, output, CURRENT_TIME);
  const second = assignTiming(entity, output, CURRENT_TIME);

  assert.deepEqual(first, second);
  assert.notEqual(first.attention_item?.timing_band, undefined);
});
