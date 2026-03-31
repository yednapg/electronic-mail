import test from 'node:test';
import assert from 'node:assert/strict';

import type { Entity } from '@electronic-mail/types';

import { classifyEntity } from './classifyEntity';

test('example 1: awaiting_reply produces the expected inline attention item', () => {
  const input: Entity = {
    id: 'entity-example-1',
    user_id: 'user-1',
    thread_id: 'thread-1',
    current_state: 'awaiting_reply',
    due_at: null,
    importance: true,
    lifecycle_state: 'active',
    created_at: '2026-03-31T08:00:00.000Z',
    updated_at: '2026-03-31T09:00:00.000Z',
  };

  const expected = {
    entity: input,
    attention_item: {
      id: 'entity-example-1',
      entity_id: 'entity-example-1',
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
      trace_id: 'entity-example-1',
      created_at: '2026-03-31T09:00:00.000Z',
    },
    suppressed: false,
    suppression_reason: null,
  } as const;

  assert.deepEqual(classifyEntity(input), expected);
});

test('example 2: informational entity is suppressed', () => {
  const input: Entity = {
    id: 'entity-example-2',
    user_id: 'user-2',
    thread_id: 'thread-2',
    current_state: 'informational',
    due_at: null,
    importance: false,
    lifecycle_state: 'active',
    created_at: '2026-03-31T10:00:00.000Z',
    updated_at: '2026-03-31T10:00:00.000Z',
  };

  const expected = {
    entity: input,
    attention_item: null,
    suppressed: true,
    suppression_reason: 'no_action_needed',
  } as const;

  assert.deepEqual(classifyEntity(input), expected);
});

test('example 3: due date present produces the expected external attention item', () => {
  const input: Entity = {
    id: 'entity-example-3',
    user_id: 'user-3',
    thread_id: 'thread-3',
    current_state: 'needs_review',
    due_at: '2026-04-03',
    importance: true,
    lifecycle_state: 'active',
    created_at: '2026-03-31T11:00:00.000Z',
    updated_at: '2026-03-31T11:00:00.000Z',
  };

  const expected = {
    entity: input,
    attention_item: {
      id: 'entity-example-3',
      entity_id: 'entity-example-3',
      user_id: 'user-3',
      need_type: 'decision',
      action_type: 'external',
      effort_level: 'deep',
      timing_band: 'today',
      action_confidence: 'medium',
      primary_action: 'open',
      fallback_action: 'open',
      title: 'Due 2026-04-03',
      why_this_is_here: 'This is here because a due date was detected for 2026-04-03.',
      trace_id: 'entity-example-3',
      created_at: '2026-03-31T11:00:00.000Z',
    },
    suppressed: false,
    suppression_reason: null,
  } as const;

  assert.deepEqual(classifyEntity(input), expected);
});

test('awaiting_reply becomes a decision with an inline action', () => {
  const entity: Entity = {
    id: 'entity-1',
    user_id: 'user-1',
    thread_id: 'thread-1',
    current_state: 'awaiting_reply',
    due_at: null,
    importance: true,
    lifecycle_state: 'active',
    created_at: '2026-03-31T08:00:00.000Z',
    updated_at: '2026-03-31T09:00:00.000Z',
  };

  const result = classifyEntity(entity);

  assert.equal(result.suppressed, false);
  assert.equal(result.suppression_reason, null);
  assert.notEqual(result.attention_item, null);
  assert.equal(result.attention_item?.need_type, 'decision');
  assert.equal(result.attention_item?.action_type, 'inline');
  assert.equal(result.attention_item?.effort_level, 'quick');
  assert.equal(result.attention_item?.action_confidence, 'high');
  assert.equal(result.attention_item?.primary_action, 'reply');
  assert.equal(result.attention_item?.created_at, '2026-03-31T09:00:00.000Z');
});

test('informational entities are suppressed', () => {
  const entity: Entity = {
    id: 'entity-2',
    user_id: 'user-2',
    thread_id: 'thread-2',
    current_state: 'informational',
    due_at: null,
    importance: false,
    lifecycle_state: 'active',
    created_at: '2026-03-31T10:00:00.000Z',
    updated_at: '2026-03-31T10:00:00.000Z',
  };

  const result = classifyEntity(entity);

  assert.equal(result.attention_item, null);
  assert.equal(result.suppressed, true);
  assert.equal(result.suppression_reason, 'no_action_needed');
});

test('completed entities are suppressed', () => {
  const entity: Entity = {
    id: 'entity-3',
    user_id: 'user-3',
    thread_id: 'thread-3',
    current_state: 'completed',
    due_at: null,
    importance: true,
    lifecycle_state: 'resolved',
    created_at: '2026-03-31T11:00:00.000Z',
    updated_at: '2026-03-31T11:00:00.000Z',
  };

  const result = classifyEntity(entity);

  assert.equal(result.attention_item, null);
  assert.equal(result.suppressed, true);
  assert.equal(result.suppression_reason, 'no_action_needed');
});

test('due_at presence makes the entity a decision', () => {
  const entity: Entity = {
    id: 'entity-4',
    user_id: 'user-4',
    thread_id: 'thread-4',
    current_state: 'informational',
    due_at: '2026-04-02',
    importance: true,
    lifecycle_state: 'active',
    created_at: '2026-03-31T12:00:00.000Z',
    updated_at: '2026-03-31T12:00:00.000Z',
  };

  const result = classifyEntity(entity);

  assert.equal(result.suppressed, false);
  assert.equal(result.attention_item?.need_type, 'decision');
  assert.equal(result.attention_item?.action_type, 'external');
  assert.equal(result.attention_item?.primary_action, 'open');
});

test('unclear important case falls back to external open', () => {
  const entity: Entity = {
    id: 'entity-5',
    user_id: 'user-5',
    thread_id: 'thread-5',
    current_state: 'needs_review',
    due_at: '2026-04-03',
    importance: true,
    lifecycle_state: 'active',
    created_at: '2026-03-31T13:00:00.000Z',
    updated_at: '2026-03-31T13:00:00.000Z',
  };

  const result = classifyEntity(entity);

  assert.equal(result.suppressed, false);
  assert.equal(result.attention_item?.action_type, 'external');
  assert.equal(result.attention_item?.action_confidence, 'medium');
  assert.equal(result.attention_item?.primary_action, 'open');
});

test.describe('decision invariants', () => {
  test('awareness is never surfaced', () => {
    const entity: Entity = {
      id: 'invariant-1',
      user_id: 'user-1',
      thread_id: 'thread-1',
      current_state: 'informational',
      due_at: null,
      importance: true,
      lifecycle_state: 'active',
      created_at: '2026-03-31T14:00:00.000Z',
      updated_at: '2026-03-31T14:00:00.000Z',
    };

    const result = classifyEntity(entity);

    assert.equal(result.attention_item, null);
    assert.equal(result.suppressed, true);
  });

  test('no clear action plus low importance is suppressed', () => {
    const entity: Entity = {
      id: 'invariant-2',
      user_id: 'user-2',
      thread_id: 'thread-2',
      current_state: 'needs_review',
      due_at: null,
      importance: false,
      lifecycle_state: 'active',
      created_at: '2026-03-31T14:10:00.000Z',
      updated_at: '2026-03-31T14:10:00.000Z',
    };

    const result = classifyEntity(entity);

    assert.equal(result.attention_item, null);
    assert.equal(result.suppressed, true);
  });

  test('no clear action plus high importance surfaces as external open', () => {
    const entity: Entity = {
      id: 'invariant-3',
      user_id: 'user-3',
      thread_id: 'thread-3',
      current_state: 'needs_review',
      due_at: null,
      importance: true,
      lifecycle_state: 'active',
      created_at: '2026-03-31T14:20:00.000Z',
      updated_at: '2026-03-31T14:20:00.000Z',
    };

    const result = classifyEntity(entity);

    assert.notEqual(result.attention_item, null);
    assert.equal(result.attention_item?.action_type, 'external');
    assert.equal(result.attention_item?.primary_action, 'open');
  });

  test('ambiguous entities do not hallucinate inline actions', () => {
    const entity: Entity = {
      id: 'invariant-4',
      user_id: 'user-4',
      thread_id: 'thread-4',
      current_state: 'needs_review',
      due_at: '2026-04-04',
      importance: true,
      lifecycle_state: 'active',
      created_at: '2026-03-31T14:30:00.000Z',
      updated_at: '2026-03-31T14:30:00.000Z',
    };

    const result = classifyEntity(entity);

    assert.notEqual(result.attention_item?.action_type, 'inline');
  });

  test('same input twice yields identical output', () => {
    const entity: Entity = {
      id: 'invariant-5',
      user_id: 'user-5',
      thread_id: 'thread-5',
      current_state: 'awaiting_rsvp',
      due_at: null,
      importance: true,
      lifecycle_state: 'active',
      created_at: '2026-03-31T14:40:00.000Z',
      updated_at: '2026-03-31T14:40:00.000Z',
    };

    const first = classifyEntity(entity);
    const second = classifyEntity(entity);

    assert.deepEqual(first, second);
  });

  test('suppressed outputs always include a suppression reason', () => {
    const entity: Entity = {
      id: 'invariant-6',
      user_id: 'user-6',
      thread_id: 'thread-6',
      current_state: 'informational',
      due_at: null,
      importance: false,
      lifecycle_state: 'active',
      created_at: '2026-03-31T14:50:00.000Z',
      updated_at: '2026-03-31T14:50:00.000Z',
    };

    const result = classifyEntity(entity);

    assert.equal(result.suppressed, true);
    assert.notEqual(result.suppression_reason, null);
  });

  test('attention item has no undefined fields when present', () => {
    const entity: Entity = {
      id: 'invariant-7',
      user_id: 'user-7',
      thread_id: 'thread-7',
      current_state: 'awaiting_reply',
      due_at: null,
      importance: true,
      lifecycle_state: 'active',
      created_at: '2026-03-31T15:00:00.000Z',
      updated_at: '2026-03-31T15:00:00.000Z',
    };

    const result = classifyEntity(entity);

    assert.notEqual(result.attention_item, null);

    for (const value of Object.values(result.attention_item ?? {})) {
      assert.notEqual(value, undefined);
    }
  });
});
