import test from 'node:test';
import assert from 'node:assert/strict';

import type { AttentionItem, Entity, FeedResponse, PipelineOutput } from '@electronic-mail/types';

import { buildFeed } from './buildFeed';

type EntityOverrides = Partial<{
  id: Entity['id'];
  user_id: Entity['user_id'];
  thread_id: string;
  current_state: Entity['current_state'];
  due_at: Entity['due_at'];
  importance: Entity['importance'];
  lifecycle_state: Entity['lifecycle_state'];
  created_at: Entity['created_at'];
  updated_at: Entity['updated_at'];
}>;

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
  const entityId = overrides.entity_id ?? 'entity-1';

  return {
    id: entityId,
    entity_id: entityId,
    user_id: 'user-1',
    need_type: 'decision',
    action_type: 'external',
    effort_level: 'quick',
    timing_band: 'today',
    action_confidence: 'high',
    primary_action: 'open',
    fallback_action: 'open',
    title: 'Attention needed',
    why_this_is_here: 'Because it needs attention.',
    trace_id: entityId,
    created_at: '2026-03-31T00:00:00.000Z',
    ...overrides,
  };
}

function createOutput(overrides: Partial<PipelineOutput> = {}): PipelineOutput {
  const entity = overrides.entity ?? createEntity();
  const attentionItem =
    overrides.attention_item === undefined
      ? createAttentionItem({ entity_id: entity.id, id: entity.id, trace_id: entity.id })
      : overrides.attention_item;

  return {
    entity,
    attention_item: attentionItem,
    suppressed: false,
    suppression_reason: null,
    ...overrides,
  };
}

function toEntityIds(feed: FeedResponse): Record<keyof FeedResponse, string[]> {
  return {
    now: feed.now.map((item) => item.entity_id),
    today: feed.today.map((item) => item.entity_id),
    worth_knowing: feed.worth_knowing.map((item) => item.entity_id),
  };
}

test('correct section placement', () => {
  const feed = buildFeed([
    createOutput({
      entity: createEntity({ id: 'entity-now', thread_id: 'thread-now', due_at: '2026-03-31T08:00:00.000Z' }),
      attention_item: createAttentionItem({ entity_id: 'entity-now', id: 'entity-now', trace_id: 'entity-now', timing_band: 'now' }),
    }),
    createOutput({
      entity: createEntity({ id: 'entity-today', thread_id: 'thread-today' }),
      attention_item: createAttentionItem({ entity_id: 'entity-today', id: 'entity-today', trace_id: 'entity-today', timing_band: 'today' }),
    }),
    createOutput({
      entity: createEntity({ id: 'entity-later', thread_id: 'thread-later', importance: false }),
      attention_item: createAttentionItem({ entity_id: 'entity-later', id: 'entity-later', trace_id: 'entity-later', timing_band: 'later' }),
    }),
  ]);

  assert.deepEqual(toEntityIds(feed), {
    now: ['entity-now'],
    today: ['entity-today'],
    worth_knowing: ['entity-later'],
  });
});

test('no duplicates across sections and higher priority wins', () => {
  const feed = buildFeed([
    createOutput({
      entity: createEntity({ id: 'entity-dup', thread_id: 'thread-dup', due_at: '2026-04-03T00:00:00.000Z' }),
      attention_item: createAttentionItem({ entity_id: 'entity-dup', id: 'entity-dup', trace_id: 'entity-dup', timing_band: 'later' }),
    }),
    createOutput({
      entity: createEntity({ id: 'entity-dup', thread_id: 'thread-dup', due_at: '2026-04-01T00:00:00.000Z' }),
      attention_item: createAttentionItem({ entity_id: 'entity-dup', id: 'entity-dup', trace_id: 'entity-dup', timing_band: 'today' }),
    }),
    createOutput({
      entity: createEntity({ id: 'entity-dup', thread_id: 'thread-dup', due_at: '2026-03-31T04:00:00.000Z' }),
      attention_item: createAttentionItem({ entity_id: 'entity-dup', id: 'entity-dup', trace_id: 'entity-dup', timing_band: 'now' }),
    }),
  ]);

  assert.deepEqual(toEntityIds(feed), {
    now: ['entity-dup'],
    today: [],
    worth_knowing: [],
  });
});

test('suppressed items and hidden items are excluded', () => {
  const feed = buildFeed([
    createOutput({
      entity: createEntity({ id: 'entity-suppressed', thread_id: 'thread-suppressed' }),
      attention_item: createAttentionItem({
        entity_id: 'entity-suppressed',
        id: 'entity-suppressed',
        trace_id: 'entity-suppressed',
        timing_band: 'today',
      }),
      suppressed: true,
      suppression_reason: 'no_action_needed',
    }),
    createOutput({
      entity: createEntity({ id: 'entity-hidden', thread_id: 'thread-hidden' }),
      attention_item: createAttentionItem({
        entity_id: 'entity-hidden',
        id: 'entity-hidden',
        trace_id: 'entity-hidden',
        timing_band: 'hidden',
      }),
    }),
    createOutput({
      entity: createEntity({ id: 'entity-null', thread_id: 'thread-null' }),
      attention_item: null,
      suppressed: false,
      suppression_reason: null,
    }),
    createOutput({
      entity: createEntity({ id: 'entity-visible', thread_id: 'thread-visible' }),
      attention_item: createAttentionItem({
        entity_id: 'entity-visible',
        id: 'entity-visible',
        trace_id: 'entity-visible',
        timing_band: 'today',
      }),
    }),
  ]);

  assert.deepEqual(toEntityIds(feed), {
    now: [],
    today: ['entity-visible'],
    worth_knowing: [],
  });
});

test('resolved items are excluded', () => {
  const feed = buildFeed([
    createOutput({
      entity: createEntity({
        id: 'entity-resolved',
        thread_id: 'thread-resolved',
        lifecycle_state: 'resolved',
      }),
      attention_item: createAttentionItem({
        entity_id: 'entity-resolved',
        id: 'entity-resolved',
        trace_id: 'entity-resolved',
        timing_band: 'today',
      }),
      suppressed: false,
      suppression_reason: null,
    }),
    createOutput({
      entity: createEntity({ id: 'entity-active', thread_id: 'thread-active' }),
      attention_item: createAttentionItem({
        entity_id: 'entity-active',
        id: 'entity-active',
        trace_id: 'entity-active',
        timing_band: 'today',
      }),
    }),
  ]);

  assert.deepEqual(toEntityIds(feed), {
    now: [],
    today: ['entity-active'],
    worth_knowing: [],
  });
});

test('sorting behavior prefers earlier due_at in now and due_at then importance in today', () => {
  const feed = buildFeed([
    createOutput({
      entity: createEntity({
        id: 'now-late',
        thread_id: 'thread-now-late',
        due_at: '2026-03-31T12:00:00.000Z',
      }),
      attention_item: createAttentionItem({
        entity_id: 'now-late',
        id: 'now-late',
        trace_id: 'now-late',
        timing_band: 'now',
      }),
    }),
    createOutput({
      entity: createEntity({
        id: 'today-no-due-important',
        thread_id: 'thread-today-no-due-important',
        due_at: null,
        importance: true,
      }),
      attention_item: createAttentionItem({
        entity_id: 'today-no-due-important',
        id: 'today-no-due-important',
        trace_id: 'today-no-due-important',
        timing_band: 'today',
      }),
    }),
    createOutput({
      entity: createEntity({
        id: 'today-no-due-low',
        thread_id: 'thread-today-no-due-low',
        due_at: null,
        importance: false,
      }),
      attention_item: createAttentionItem({
        entity_id: 'today-no-due-low',
        id: 'today-no-due-low',
        trace_id: 'today-no-due-low',
        timing_band: 'today',
      }),
    }),
    createOutput({
      entity: createEntity({
        id: 'today-early',
        thread_id: 'thread-today-early',
        due_at: '2026-04-01T00:00:00.000Z',
      }),
      attention_item: createAttentionItem({
        entity_id: 'today-early',
        id: 'today-early',
        trace_id: 'today-early',
        timing_band: 'today',
      }),
    }),
    createOutput({
      entity: createEntity({
        id: 'now-early',
        thread_id: 'thread-now-early',
        due_at: '2026-03-31T04:00:00.000Z',
      }),
      attention_item: createAttentionItem({
        entity_id: 'now-early',
        id: 'now-early',
        trace_id: 'now-early',
        timing_band: 'now',
      }),
    }),
  ]);

  assert.deepEqual(feed.now.map((item) => item.entity_id), ['now-early', 'now-late']);
  assert.deepEqual(feed.today.map((item) => item.entity_id), [
    'today-early',
    'today-no-due-important',
    'today-no-due-low',
  ]);
});

test('mixed batch scenario produces the expected feed', () => {
  const feed = buildFeed([
    createOutput({
      entity: createEntity({
        id: 'reply-now',
        thread_id: 'thread-reply-now',
        due_at: '2026-03-31T03:00:00.000Z',
        importance: true,
      }),
      attention_item: createAttentionItem({
        entity_id: 'reply-now',
        id: 'reply-now',
        trace_id: 'reply-now',
        timing_band: 'now',
        primary_action: 'reply',
      }),
    }),
    createOutput({
      entity: createEntity({
        id: 'bill-today',
        thread_id: 'thread-bill-today',
        due_at: '2026-04-01T00:00:00.000Z',
        importance: true,
      }),
      attention_item: createAttentionItem({
        entity_id: 'bill-today',
        id: 'bill-today',
        trace_id: 'bill-today',
        timing_band: 'today',
      }),
    }),
    createOutput({
      entity: createEntity({
        id: 'newsletter-later',
        thread_id: 'thread-newsletter-later',
        due_at: null,
        importance: false,
      }),
      attention_item: createAttentionItem({
        entity_id: 'newsletter-later',
        id: 'newsletter-later',
        trace_id: 'newsletter-later',
        timing_band: 'later',
      }),
    }),
    createOutput({
      entity: createEntity({
        id: 'resolved-drop',
        thread_id: 'thread-resolved-drop',
        lifecycle_state: 'resolved',
      }),
      attention_item: createAttentionItem({
        entity_id: 'resolved-drop',
        id: 'resolved-drop',
        trace_id: 'resolved-drop',
        timing_band: 'today',
      }),
    }),
    createOutput({
      entity: createEntity({
        id: 'duplicate-bill',
        thread_id: 'thread-duplicate-bill',
        due_at: '2026-04-02T00:00:00.000Z',
      }),
      attention_item: createAttentionItem({
        entity_id: 'duplicate-bill',
        id: 'duplicate-bill',
        trace_id: 'duplicate-bill',
        timing_band: 'later',
      }),
    }),
    createOutput({
      entity: createEntity({
        id: 'duplicate-bill',
        thread_id: 'thread-duplicate-bill',
        due_at: '2026-03-31T06:00:00.000Z',
      }),
      attention_item: createAttentionItem({
        entity_id: 'duplicate-bill',
        id: 'duplicate-bill',
        trace_id: 'duplicate-bill',
        timing_band: 'today',
      }),
    }),
  ]);

  assert.deepEqual(toEntityIds(feed), {
    now: ['reply-now'],
    today: ['duplicate-bill', 'bill-today'],
    worth_knowing: ['newsletter-later'],
  });
});
