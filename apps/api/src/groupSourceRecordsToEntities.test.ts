import test, { describe } from 'node:test';
import assert from 'node:assert/strict';

import type { SourceRecord } from '@electronic-mail/types';

import {
  computeNormalizedGroupId,
  groupSourceRecordsToEntities,
  groupSourceRecordsToEntityBundles,
} from './groupSourceRecordsToEntities';

test('example 1: same thread_id collapses into one informational entity', () => {
  const input: SourceRecord[] = [
    {
      id: 'g1',
      user_id: 'u1',
      source: 'gmail',
      thread_id: 'thread-example-1',
      raw_payload: {
        from: 'no-reply@updates.example.com',
        subject: 'Product update',
        body: 'Weekly roundup only.',
      },
      received_at: '2026-03-31T08:00:00.000Z',
    },
    {
      id: 'g2',
      user_id: 'u1',
      source: 'gmail',
      thread_id: 'thread-example-1',
      raw_payload: {
        body: 'More product updates.',
      },
      received_at: '2026-03-31T09:00:00.000Z',
    },
  ];

  const expected = [
    {
      id: 'thread-example-1',
      user_id: 'u1',
      thread_id: 'thread-example-1',
      current_state: 'informational',
      due_at: null,
      importance: false,
      lifecycle_state: 'active',
      created_at: '2026-03-31T08:00:00.000Z',
      updated_at: '2026-03-31T09:00:00.000Z',
    },
  ] as const;

  assert.deepEqual(groupSourceRecordsToEntities(input), expected);
});

test('example 2: RSVP thread derives awaiting_rsvp with due_at', () => {
  const input: SourceRecord[] = [
    {
      id: 'g3',
      user_id: 'u2',
      source: 'gmail',
      thread_id: 'thread-example-2',
      raw_payload: {
        from: 'Taylor Example <taylor@example.com>',
        subject: 'Dinner invitation',
        body: 'Please RSVP by 2026-04-05.',
      },
      received_at: '2026-03-31T10:00:00.000Z',
    },
  ];

  const expected = [
    {
      id: 'thread-example-2',
      user_id: 'u2',
      thread_id: 'thread-example-2',
      current_state: 'awaiting_rsvp',
      due_at: '2026-04-05',
      importance: true,
      lifecycle_state: 'active',
      created_at: '2026-03-31T10:00:00.000Z',
      updated_at: '2026-03-31T10:00:00.000Z',
    },
  ] as const;

  assert.deepEqual(groupSourceRecordsToEntities(input), expected);
});

test('example 3: completed payment thread derives resolved entity', () => {
  const input: SourceRecord[] = [
    {
      id: 'g4',
      user_id: 'u3',
      source: 'gmail',
      thread_id: 'thread-example-3',
      raw_payload: {
        from: 'billing@vendor.com',
        subject: 'Payment completed',
        body: 'Your invoice is paid. Receipt attached.',
      },
      received_at: '2026-03-31T11:00:00.000Z',
    },
  ];

  const expected = [
    {
      id: 'thread-example-3',
      user_id: 'u3',
      thread_id: 'thread-example-3',
      current_state: 'completed',
      due_at: null,
      importance: true,
      lifecycle_state: 'resolved',
      created_at: '2026-03-31T11:00:00.000Z',
      updated_at: '2026-03-31T11:00:00.000Z',
    },
  ] as const;

  assert.deepEqual(groupSourceRecordsToEntities(input), expected);
});

test('creates exactly one entity per thread_id', () => {
  const input: SourceRecord[] = [
    {
      id: '1',
      user_id: 'u1',
      source: 'gmail',
      thread_id: 't1',
      raw_payload: {},
      received_at: '2024-01-01',
    },
    {
      id: '2',
      user_id: 'u1',
      source: 'gmail',
      thread_id: 't1',
      raw_payload: {},
      received_at: '2024-01-01',
    },
  ];

  const result = groupSourceRecordsToEntities(input);

  assert.equal(result.length, 1);
  assert.equal(result[0].thread_id, 't1');
});

test('groups multiple records from the same thread into one entity', () => {
  const records: SourceRecord[] = [
    {
      id: 'src-1',
      user_id: 'user-1',
      source: 'gmail',
      thread_id: 'thread-1',
      raw_payload: {
        from: 'Alex Example <alex@example.com>',
        subject: 'Can you reply today?',
        body: 'Please reply when you can.',
      },
      received_at: '2026-03-31T08:00:00.000Z',
    },
    {
      id: 'src-2',
      user_id: 'user-1',
      source: 'gmail',
      thread_id: 'thread-1',
      raw_payload: {
        from: 'Alex Example <alex@example.com>',
        body: 'Let me know what you think.',
      },
      received_at: '2026-03-31T09:00:00.000Z',
    },
  ];

  const entities = groupSourceRecordsToEntities(records);

  assert.equal(entities.length, 1);
  assert.deepEqual(entities[0], {
    id: 'thread-1',
    user_id: 'user-1',
    thread_id: 'thread-1',
    current_state: 'awaiting_reply',
    due_at: null,
    importance: true,
    lifecycle_state: 'active',
    created_at: '2026-03-31T08:00:00.000Z',
    updated_at: '2026-03-31T09:00:00.000Z',
  });
});

test('derives awaiting_rsvp and due_at for calendar-related threads', () => {
  const records: SourceRecord[] = [
    {
      id: 'src-3',
      user_id: 'user-2',
      source: 'gmail',
      thread_id: 'thread-2',
      raw_payload: {
        from: 'Morgan Example <morgan@example.com>',
        subject: 'Team offsite invitation',
        body: 'Please RSVP by 2026-04-05 for the event.',
      },
      received_at: '2026-03-31T10:00:00.000Z',
    },
  ];

  const entities = groupSourceRecordsToEntities(records);

  assert.equal(entities.length, 1);
  assert.equal(entities[0].current_state, 'awaiting_rsvp');
  assert.equal(entities[0].due_at, '2026-04-05');
  assert.equal(entities[0].importance, true);
  assert.equal(entities[0].lifecycle_state, 'active');
});

test('marks clearly completed threads as resolved', () => {
  const records: SourceRecord[] = [
    {
      id: 'src-4',
      user_id: 'user-3',
      source: 'gmail',
      thread_id: 'thread-3',
      raw_payload: {
        from: 'billing@vendor.com',
        subject: 'Invoice paid',
        body: 'Your payment is completed. Receipt attached.',
      },
      received_at: '2026-03-31T11:00:00.000Z',
    },
  ];

  const entities = groupSourceRecordsToEntities(records);

  assert.equal(entities.length, 1);
  assert.equal(entities[0].current_state, 'completed');
  assert.equal(entities[0].lifecycle_state, 'resolved');
  assert.equal(entities[0].importance, true);
});

test('keeps clearly informational system mail as informational and not important', () => {
  const records: SourceRecord[] = [
    {
      id: 'src-5',
      user_id: 'user-4',
      source: 'gmail',
      thread_id: 'thread-4',
      raw_payload: {
        from: 'no-reply@updates.example.com',
        subject: 'Weekly product newsletter',
        body: 'This is your weekly roundup.',
      },
      received_at: '2026-03-31T12:00:00.000Z',
    },
  ];

  const entities = groupSourceRecordsToEntities(records);

  assert.equal(entities.length, 1);
  assert.equal(entities[0].current_state, 'informational');
  assert.equal(entities[0].importance, false);
  assert.equal(entities[0].lifecycle_state, 'active');
});

describe('cross-thread grouping', () => {
  test('same bank reminders collapse into one entity', () => {
    const records: SourceRecord[] = [
      {
        id: 'northstar-1',
        user_id: 'user-northstar',
        source: 'gmail',
        thread_id: 'thread-northstar-1',
        raw_payload: {
          from: 'alerts@northstar.com',
          subject: 'Bill due reminder',
          body: 'Payment due on 2026-04-05. Card ending 1234.',
        },
        received_at: '2026-03-30T08:00:00.000Z',
      },
      {
        id: 'northstar-2',
        user_id: 'user-northstar',
        source: 'gmail',
        thread_id: 'thread-northstar-2',
        raw_payload: {
          from: 'reminders@northstar.com',
          subject: 'Payment due reminder',
          body: 'Statement due on 2026-04-05. Card ending 1234.',
        },
        received_at: '2026-04-01T08:00:00.000Z',
      },
      {
        id: 'northstar-3',
        user_id: 'user-northstar',
        source: 'gmail',
        thread_id: 'thread-northstar-3',
        raw_payload: {
          from: 'billing@northstar.com',
          subject: 'Last bill due reminder',
          body: 'Bill due on 2026-04-05. Card ending 1234.',
        },
        received_at: '2026-04-03T08:00:00.000Z',
      },
    ];

    const entities = groupSourceRecordsToEntities(records);

    assert.equal(entities.length, 1);
    assert.ok('group_id' in entities[0], 'Expected cross-thread merge to use group_id');
    assert.equal(entities[0].due_at, '2026-04-05');
    assert.equal(entities[0].importance, true);
    assert.equal(computeNormalizedGroupId(records[0]), 'group:northstar.com:bill:due');
    assert.equal(computeNormalizedGroupId(records[1]), 'group:northstar.com:due:payment');
  });

  test('different banks remain separate', () => {
    const records: SourceRecord[] = [
      {
        id: 'bank-1',
        user_id: 'user-banks',
        source: 'gmail',
        thread_id: 'thread-northstar-bank',
        raw_payload: {
          from: 'alerts@northstar.com',
          subject: 'Bill due reminder',
          body: 'Payment due on 2026-04-05.',
        },
        received_at: '2026-03-31T08:00:00.000Z',
      },
      {
        id: 'bank-2',
        user_id: 'user-banks',
        source: 'gmail',
        thread_id: 'thread-hsbc-bank',
        raw_payload: {
          from: 'alerts@bank.example',
          subject: 'Bill due reminder',
          body: 'Payment due on 2026-04-05.',
        },
        received_at: '2026-04-01T08:00:00.000Z',
      },
    ];

    const entities = groupSourceRecordsToEntities(records);

    assert.equal(entities.length, 2);
    assert.equal(new Set(entities.map((entity) => entity.id)).size, 2);
  });

  test('same subject but different domains do not merge', () => {
    const records: SourceRecord[] = [
      {
        id: 'domain-1',
        user_id: 'user-domains',
        source: 'gmail',
        thread_id: 'thread-domain-1',
        raw_payload: {
          from: 'alerts@northstar.com',
          subject: 'Payment due reminder',
          body: 'Statement due on 2026-04-05.',
        },
        received_at: '2026-03-31T08:00:00.000Z',
      },
      {
        id: 'domain-2',
        user_id: 'user-domains',
        source: 'gmail',
        thread_id: 'thread-domain-2',
        raw_payload: {
          from: 'alerts@vendor.com',
          subject: 'Payment due reminder',
          body: 'Statement due on 2026-04-05.',
        },
        received_at: '2026-04-01T08:00:00.000Z',
      },
    ];

    const entities = groupSourceRecordsToEntities(records);

    assert.equal(entities.length, 2);
  });

  test('same domain but very different subjects do not merge', () => {
    const records: SourceRecord[] = [
      {
        id: 'subject-1',
        user_id: 'user-subjects',
        source: 'gmail',
        thread_id: 'thread-subject-1',
        raw_payload: {
          from: 'alerts@northstar.com',
          subject: 'Bill due reminder',
          body: 'Payment due on 2026-04-05.',
        },
        received_at: '2026-03-31T08:00:00.000Z',
      },
      {
        id: 'subject-2',
        user_id: 'user-subjects',
        source: 'gmail',
        thread_id: 'thread-subject-2',
        raw_payload: {
          from: 'alerts@northstar.com',
          subject: 'Travel rewards update',
          body: 'New offers for your account.',
        },
        received_at: '2026-04-01T08:00:00.000Z',
      },
    ];

    const entities = groupSourceRecordsToEntities(records);

    assert.equal(entities.length, 2);
    assert.equal(computeNormalizedGroupId(records[0]), 'group:northstar.com:bill:due');
    assert.equal(computeNormalizedGroupId(records[1]), 'thread:thread-subject-2');
  });
});

test('entity bundles preserve grouped source records for downstream decision input', () => {
  const records: SourceRecord[] = [
    {
      id: 'bundle-1',
      user_id: 'user-1',
      source: 'gmail',
      thread_id: 'thread-bundle',
      raw_payload: {
        subject: 'Please review this proposal',
        body: 'Can you review the attached proposal today?',
        from: 'rahul@example.com',
      },
      received_at: '2026-04-03T06:00:00.000Z',
    },
    {
      id: 'bundle-2',
      user_id: 'user-1',
      source: 'gmail',
      thread_id: 'thread-bundle',
      raw_payload: {
        subject: 'Re: Please review this proposal',
        body: 'Following up on the proposal review request.',
        from: 'rahul@example.com',
      },
      received_at: '2026-04-03T07:00:00.000Z',
    },
  ];

  const bundles = groupSourceRecordsToEntityBundles(records);

  assert.equal(bundles.length, 1);
  assert.equal(bundles[0].entity.id, 'thread-bundle');
  assert.equal(bundles[0].records.length, 2);
  assert.equal(bundles[0].records[0].id, 'bundle-1');
  assert.equal(bundles[0].records[1].id, 'bundle-2');
});

test('email received_at metadata does not create a false due_at without an explicit due signal', () => {
  const records: SourceRecord[] = [
    {
      id: 'received-at-only',
      user_id: 'user-1',
      source: 'gmail',
      thread_id: 'thread-received-at-only',
      raw_payload: {
        subject: 'Your GitHub Pro discount ends April 6',
        body: 'This is a product update with no deadline request.',
        from: 'support@github.com',
        received_at: '2026-04-03T06:00:00.000Z',
      },
      received_at: '2026-04-03T06:00:00.000Z',
    },
  ];

  const entities = groupSourceRecordsToEntities(records);

  assert.equal(entities.length, 1);
  assert.equal(entities[0].due_at, null);
});
