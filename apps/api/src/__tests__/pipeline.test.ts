import assert from 'node:assert/strict';
import test, { describe } from 'node:test';

import type { FeedResponse, SourceRecord } from '@decision-pipeline/types';

import { assignTiming } from '../assignTiming';
import { buildFeed } from '../buildFeed';
import { classifyEntity } from '../classifyEntity';
import { groupSourceRecordsToEntities } from '../groupSourceRecordsToEntities';
import { validateFeed } from './helpers';

const CURRENT_TIME = '2026-02-01T09:00:00.000+05:30';

describe('pipeline determinism', () => {
  test('multiple emails in the same thread collapse into one entity and one visible item', () => {
    const records: SourceRecord[] = [
      {
        id: '1',
        user_id: 'u1',
        source: 'gmail',
        thread_id: 'thread-reply',
        raw_payload: {
          from: 'priya@example.com',
          subject: 'Design review',
          body: 'Can you reply with your final comments?',
        },
        received_at: '2026-02-01T08:00:00.000+05:30',
      },
      {
        id: '2',
        user_id: 'u1',
        source: 'gmail',
        thread_id: 'thread-reply',
        raw_payload: {
          from: 'priya@example.com',
          subject: 'Design review follow-up',
          body: 'Please reply before the team sync.',
        },
        received_at: '2026-02-01T08:15:00.000+05:30',
      },
    ];

    const entities = groupSourceRecordsToEntities(records);
    const feed = runPipeline(records);

    assert.equal(entities.length, 1);
    assert.equal(feed.now.length + feed.today.length + feed.worth_knowing.length, 1);
    validateFeed(feed);
  });

  test('repeated reminders collapse into one entity', () => {
    const records: SourceRecord[] = [
      {
        id: '1',
        user_id: 'u1',
        source: 'gmail',
        thread_id: 'thread-reminder-a',
        raw_payload: {
          from: 'billing@bank.com',
          subject: 'Axis Bank card bill due',
          body: 'Payment due 2026-02-01T12:00:00.000+05:30.',
        },
        received_at: '2026-02-01T07:30:00.000+05:30',
      },
      {
        id: '2',
        user_id: 'u1',
        source: 'gmail',
        thread_id: 'thread-reminder-b',
        raw_payload: {
          from: 'billing@bank.com',
          subject: 'Axis Bank card bill due today',
          body: 'Reminder: payment due 2026-02-01T12:00:00.000+05:30.',
        },
        received_at: '2026-02-01T08:00:00.000+05:30',
      },
    ];

    const entities = groupSourceRecordsToEntities(records);
    const feed = runPipeline(records);

    assert.equal(entities.length, 1);
    assert.equal(feed.now.length, 1);
    assert.equal(feed.now[0]?.entity_id, entities[0]?.id);
    validateFeed(feed);
  });

  test('invalid or noisy payloads are handled safely', () => {
    const records: SourceRecord[] = [
      {
        id: 'bad-1',
        user_id: 'u1',
        source: 'gmail',
        thread_id: 'thread-noise',
        raw_payload: {
          from: 42,
          subject: null,
          body: ['unexpected', { nested: ['values', 99] }],
          misc: { weird: true },
        },
        received_at: '2026-02-01T08:00:00.000+05:30',
      },
    ];

    assert.doesNotThrow(() => groupSourceRecordsToEntities(records));

    const feed = runPipeline(records);
    validateFeed(feed);
    assert.equal(feed.now.length + feed.today.length + feed.worth_knowing.length, 0);
  });
});

function runPipeline(records: SourceRecord[]): FeedResponse {
  const outputs = groupSourceRecordsToEntities(records).map((entity) =>
    assignTiming(entity, classifyEntity(entity), CURRENT_TIME),
  );

  return buildFeed(outputs);
}

