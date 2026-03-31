import test from 'node:test';
import assert from 'node:assert/strict';

import type { SourceRecord } from '@decision-pipeline/types';

import { groupSourceRecordsToEntities } from './groupSourceRecordsToEntities';

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
