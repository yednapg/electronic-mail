import test, { describe } from 'node:test';
import assert from 'node:assert/strict';

import type { AttentionItem, Entity, FeedResponse, PipelineOutput, SourceRecord } from '@decision-pipeline/types';

import { assignTiming } from './assignTiming';
import { buildFeed } from './buildFeed';
import { classifyEntity } from './classifyEntity';
import { groupSourceRecordsToEntities } from './groupSourceRecordsToEntities';

const CURRENT_TIME = '2026-03-31T00:00:00.000Z';

function runPipelineOutputs(
  records: SourceRecord[],
  currentTime: string = CURRENT_TIME,
  transformEntity: (entity: Entity) => Entity = (entity) => entity,
): PipelineOutput[] {
  return groupSourceRecordsToEntities(records).map((entity) => {
    const transformedEntity = transformEntity(entity);
    const classified = classifyEntity(transformedEntity);

    return assignTiming(transformedEntity, classified, currentTime);
  });
}

function runPipeline(
  records: SourceRecord[],
  currentTime: string = CURRENT_TIME,
  transformEntity: (entity: Entity) => Entity = (entity) => entity,
): FeedResponse {
  const outputs = runPipelineOutputs(records, currentTime, transformEntity);

  return buildFeed(outputs);
}

function summarizeFeed(feed: FeedResponse): Record<keyof FeedResponse, string[]> {
  return {
    now: feed.now.map((item) => item.entity_id),
    today: feed.today.map((item) => item.entity_id),
    worth_knowing: feed.worth_knowing.map((item) => item.entity_id),
  };
}

function findVisibleItem(feed: FeedResponse, entityId: string): AttentionItem | undefined {
  return [...feed.now, ...feed.today, ...feed.worth_knowing].find(
    (item) => item.entity_id === entityId,
  );
}

function collectFeedEntityIds(feed: FeedResponse): string[] {
  return [...feed.now, ...feed.today, ...feed.worth_knowing].map((item) => item.entity_id);
}

function sortFeed(feed: FeedResponse): FeedResponse {
  return {
    now: [...feed.now].sort((left, right) => left.entity_id.localeCompare(right.entity_id)),
    today: [...feed.today].sort((left, right) => left.entity_id.localeCompare(right.entity_id)),
    worth_knowing: [...feed.worth_knowing].sort((left, right) =>
      left.entity_id.localeCompare(right.entity_id),
    ),
  };
}

function reorderRecordsDeterministically(records: SourceRecord[]): SourceRecord[] {
  return [...records].sort((left, right) =>
    `${right.thread_id}:${right.id}`.localeCompare(`${left.thread_id}:${left.id}`),
  );
}

function createRecord(
  id: string,
  threadId: string,
  receivedAt: string,
  rawPayload: Record<string, unknown>,
  userId: string = 'user-generated',
): SourceRecord {
  return {
    id,
    user_id: userId,
    source: 'gmail',
    thread_id: threadId,
    raw_payload: rawPayload,
    received_at: receivedAt,
  };
}

function buildOutputMaps(outputs: PipelineOutput[]): {
  outputByEntityId: Map<string, PipelineOutput>;
  timingBandByEntityId: Map<string, NonNullable<AttentionItem['timing_band']>>;
} {
  const outputByEntityId = new Map<string, PipelineOutput>();
  const timingBandByEntityId = new Map<string, NonNullable<AttentionItem['timing_band']>>();

  for (const output of outputs) {
    outputByEntityId.set(output.entity.id, output);

    if (output.attention_item !== null) {
      timingBandByEntityId.set(output.entity.id, output.attention_item.timing_band);
    }
  }

  return {
    outputByEntityId,
    timingBandByEntityId,
  };
}

test('event flow surfaces one RSVP item in today', () => {
  const records: SourceRecord[] = [
    {
      id: 'event-1',
      user_id: 'user-events',
      source: 'gmail',
      thread_id: 'thread-event',
      raw_payload: {
        from: 'events@conference.com',
        subject: 'Registration confirmed for annual summit',
        body: 'Your registration is confirmed.',
      },
      received_at: '2026-03-20T09:00:00.000Z',
    },
    {
      id: 'event-2',
      user_id: 'user-events',
      source: 'gmail',
      thread_id: 'thread-event',
      raw_payload: {
        from: 'events@conference.com',
        subject: 'Reminder for annual summit',
        body: 'Calendar invite attached.',
      },
      received_at: '2026-03-29T09:00:00.000Z',
    },
    {
      id: 'event-3',
      user_id: 'user-events',
      source: 'gmail',
      thread_id: 'thread-event',
      raw_payload: {
        from: 'events@conference.com',
        subject: 'RSVP required for annual summit',
        body: 'Please RSVP by 2026-04-02T00:00:00.000Z.',
      },
      received_at: '2026-03-30T09:00:00.000Z',
    },
  ];

  const feed = runPipeline(records);
  const item = findVisibleItem(feed, 'thread-event');

  assert.deepEqual(summarizeFeed(feed), {
    now: [],
    today: ['thread-event'],
    worth_knowing: [],
  });
  assert.equal(item?.primary_action, 'confirm');
  assert.equal(item?.title, 'RSVP needed');
  assert.equal(
    item?.why_this_is_here,
    'This is here because the thread appears to need an RSVP.',
  );
});

test('billing flow surfaces in today', () => {
  const records: SourceRecord[] = [
    {
      id: 'bill-1',
      user_id: 'user-billing',
      source: 'gmail',
      thread_id: 'thread-bill',
      raw_payload: {
        from: 'billing@vendor.com',
        subject: 'Invoice #123',
        body: 'Your amount due is 2026-04-02T00:00:00.000Z.',
      },
      received_at: '2026-03-17T08:00:00.000Z',
    },
  ];

  const feed = runPipeline(records);
  const item = findVisibleItem(feed, 'thread-bill');

  assert.deepEqual(summarizeFeed(feed), {
    now: [],
    today: ['thread-bill'],
    worth_knowing: [],
  });
  assert.equal(item?.primary_action, 'open');
  assert.equal(item?.title, 'Due 2026-04-02T00:00:00.000Z');
  assert.equal(
    item?.why_this_is_here,
    'This is here because a due date was detected for 2026-04-02T00:00:00.000Z.',
  );
});

test('newsletter noise never appears in the final feed', () => {
  const records: SourceRecord[] = [
    {
      id: 'news-1',
      user_id: 'user-news',
      source: 'gmail',
      thread_id: 'thread-news',
      raw_payload: {
        from: 'no-reply@updates.example.com',
        subject: 'Weekly product newsletter',
        body: 'This is your weekly roundup.',
      },
      received_at: '2026-03-31T08:00:00.000Z',
    },
  ];

  const feed = runPipeline(records);

  assert.deepEqual(summarizeFeed(feed), {
    now: [],
    today: [],
    worth_knowing: [],
  });
  assert.equal(findVisibleItem(feed, 'thread-news'), undefined);
});

test('PR review email appears in today as external open', () => {
  const records: SourceRecord[] = [
    {
      id: 'pr-1',
      user_id: 'user-pr',
      source: 'gmail',
      thread_id: 'thread-pr',
      raw_payload: {
        from: 'notifications@github.com',
        subject: 'Review requested on PR #42',
        body: 'Please review PR #42 by 2026-04-02T00:00:00.000Z.',
      },
      received_at: '2026-03-31T07:00:00.000Z',
    },
  ];

  const feed = runPipeline(records);
  const item = findVisibleItem(feed, 'thread-pr');

  assert.deepEqual(summarizeFeed(feed), {
    now: [],
    today: ['thread-pr'],
    worth_knowing: [],
  });
  assert.equal(item?.primary_action, 'open');
  assert.equal(item?.title, 'Due 2026-04-02T00:00:00.000Z');
});

test('completed flow stays absent from the final feed', () => {
  const records: SourceRecord[] = [
    {
      id: 'complete-1',
      user_id: 'user-complete',
      source: 'gmail',
      thread_id: 'thread-complete',
      raw_payload: {
        from: 'billing@vendor.com',
        subject: 'Payment successful',
        body: 'Your payment is completed. Receipt attached.',
      },
      received_at: '2026-03-31T09:00:00.000Z',
    },
  ];

  const feed = runPipeline(records);

  assert.deepEqual(summarizeFeed(feed), {
    now: [],
    today: [],
    worth_knowing: [],
  });
  assert.equal(findVisibleItem(feed, 'thread-complete'), undefined);
});

test('resolved lifecycle stays absent from the final feed', () => {
  const records: SourceRecord[] = [
    {
      id: 'conflict-1',
      user_id: 'user-conflict',
      source: 'gmail',
      thread_id: 'thread-conflict',
      raw_payload: {
        from: 'alice@example.com',
        subject: 'Can you reply on this?',
        body: 'Please reply when you can.',
      },
      received_at: '2026-03-31T10:00:00.000Z',
    },
  ];

  const feed = runPipeline(records, CURRENT_TIME, (entity) => ({
    ...entity,
    lifecycle_state: 'resolved',
  }));

  assert.deepEqual(summarizeFeed(feed), {
    now: [],
    today: [],
    worth_knowing: [],
  });
  assert.equal(findVisibleItem(feed, 'thread-conflict'), undefined);
});

test('multiple unrelated threads land in the right sections with no duplicates', () => {
  const records: SourceRecord[] = [
    {
      id: 'batch-1',
      user_id: 'user-batch',
      source: 'gmail',
      thread_id: 'thread-a',
      raw_payload: {
        from: 'events@conference.com',
        subject: 'RSVP required',
        body: 'Please RSVP by 2026-04-02T00:00:00.000Z.',
      },
      received_at: '2026-03-31T01:00:00.000Z',
    },
    {
      id: 'batch-2',
      user_id: 'user-batch',
      source: 'gmail',
      thread_id: 'thread-b',
      raw_payload: {
        from: 'billing@vendor.com',
        subject: 'Invoice #456',
        body: 'Amount due 2026-04-05T00:00:00.000Z.',
      },
      received_at: '2026-03-31T02:00:00.000Z',
    },
    {
      id: 'batch-3',
      user_id: 'user-batch',
      source: 'gmail',
      thread_id: 'thread-c',
      raw_payload: {
        from: 'no-reply@updates.example.com',
        subject: 'Newsletter',
        body: 'Weekly roundup.',
      },
      received_at: '2026-03-31T03:00:00.000Z',
    },
    {
      id: 'batch-4',
      user_id: 'user-batch',
      source: 'gmail',
      thread_id: 'thread-d',
      raw_payload: {
        from: 'billing@vendor.com',
        subject: 'Payment successful',
        body: 'Payment completed.',
      },
      received_at: '2026-03-31T04:00:00.000Z',
    },
    {
      id: 'batch-5',
      user_id: 'user-batch',
      source: 'gmail',
      thread_id: 'thread-e',
      raw_payload: {
        from: 'alice@example.com',
        subject: 'Can you reply?',
        body: 'Please reply by end of day.',
      },
      received_at: '2026-03-31T05:00:00.000Z',
    },
    {
      id: 'batch-6',
      user_id: 'user-batch',
      source: 'gmail',
      thread_id: 'thread-f',
      raw_payload: {
        from: 'billing@vendor.com',
        subject: 'Overdue invoice',
        body: 'Amount due 2026-03-30T00:00:00.000Z.',
      },
      received_at: '2026-03-31T06:00:00.000Z',
    },
  ];

  const feed = runPipeline(records);
  const visibleIds = [...feed.now, ...feed.today, ...feed.worth_knowing].map(
    (item) => item.entity_id,
  );

  assert.deepEqual(summarizeFeed(feed), {
    now: ['thread-f'],
    today: ['thread-a', 'thread-e'],
    worth_knowing: ['thread-b'],
  });
  assert.equal(new Set(visibleIds).size, visibleIds.length);
  assert.equal(findVisibleItem(feed, 'thread-a')?.primary_action, 'confirm');
  assert.equal(findVisibleItem(feed, 'thread-e')?.primary_action, 'reply');
  assert.equal(findVisibleItem(feed, 'thread-b')?.primary_action, 'open');
  assert.equal(findVisibleItem(feed, 'thread-c'), undefined);
  assert.equal(findVisibleItem(feed, 'thread-d'), undefined);
});

test('unknown state but important still surfaces in today', () => {
  const records: SourceRecord[] = [
    {
      id: 'unknown-1',
      user_id: 'user-unknown',
      source: 'gmail',
      thread_id: 'thread-unknown',
      raw_payload: {
        from: 'alice@example.com',
        subject: 'This needs review',
        body: 'Please take a look.',
      },
      received_at: '2026-03-31T11:00:00.000Z',
    },
  ];

  const feed = runPipeline(records, CURRENT_TIME, (entity) => ({
    ...entity,
    current_state: 'random_unknown_state',
  }));
  const item = findVisibleItem(feed, 'thread-unknown');

  assert.deepEqual(summarizeFeed(feed), {
    now: [],
    today: ['thread-unknown'],
    worth_knowing: [],
  });
  assert.equal(item?.primary_action, 'open');
  assert.equal(item?.title, 'Informational item');
});

test('past due item lands in now', () => {
  const records: SourceRecord[] = [
    {
      id: 'past-1',
      user_id: 'user-past',
      source: 'gmail',
      thread_id: 'thread-past',
      raw_payload: {
        from: 'billing@vendor.com',
        subject: 'Invoice overdue',
        body: 'Amount due 2026-03-30T00:00:00.000Z.',
      },
      received_at: '2026-03-31T12:00:00.000Z',
    },
  ];

  const feed = runPipeline(records);
  const item = findVisibleItem(feed, 'thread-past');

  assert.deepEqual(summarizeFeed(feed), {
    now: ['thread-past'],
    today: [],
    worth_knowing: [],
  });
  assert.equal(item?.primary_action, 'open');
});

test('full pipeline feed is deterministic across repeated runs', () => {
  const records: SourceRecord[] = [
    {
      id: 'det-1',
      user_id: 'user-det',
      source: 'gmail',
      thread_id: 'thread-det',
      raw_payload: {
        from: 'events@conference.com',
        subject: 'RSVP required',
        body: 'Please RSVP by 2026-04-02T00:00:00.000Z.',
      },
      received_at: '2026-03-31T13:00:00.000Z',
    },
    {
      id: 'det-2',
      user_id: 'user-det',
      source: 'gmail',
      thread_id: 'thread-det-2',
      raw_payload: {
        from: 'no-reply@updates.example.com',
        subject: 'Newsletter',
        body: 'Weekly roundup.',
      },
      received_at: '2026-03-31T14:00:00.000Z',
    },
  ];

  const first = runPipeline(records);
  const second = runPipeline(records);

  assert.deepEqual(first, second);
});

describe('feed invariants', () => {
  const records: SourceRecord[] = [
    {
      id: 'inv-1',
      user_id: 'user-invariants',
      source: 'gmail',
      thread_id: 'thread-now',
      raw_payload: {
        from: 'billing@vendor.com',
        subject: 'Invoice overdue',
        body: 'Amount due 2026-03-30T00:00:00.000Z.',
      },
      received_at: '2026-03-31T01:00:00.000Z',
    },
    {
      id: 'inv-2',
      user_id: 'user-invariants',
      source: 'gmail',
      thread_id: 'thread-today',
      raw_payload: {
        from: 'events@conference.com',
        subject: 'RSVP required',
        body: 'Please RSVP by 2026-04-02T00:00:00.000Z.',
      },
      received_at: '2026-03-31T02:00:00.000Z',
    },
    {
      id: 'inv-3',
      user_id: 'user-invariants',
      source: 'gmail',
      thread_id: 'thread-later',
      raw_payload: {
        from: 'billing@vendor.com',
        subject: 'Invoice #789',
        body: 'Amount due 2026-04-05T00:00:00.000Z.',
      },
      received_at: '2026-03-31T03:00:00.000Z',
    },
    {
      id: 'inv-4',
      user_id: 'user-invariants',
      source: 'gmail',
      thread_id: 'thread-suppressed',
      raw_payload: {
        from: 'no-reply@updates.example.com',
        subject: 'Newsletter',
        body: 'Weekly roundup.',
      },
      received_at: '2026-03-31T04:00:00.000Z',
    },
    {
      id: 'inv-5',
      user_id: 'user-invariants',
      source: 'gmail',
      thread_id: 'thread-resolved',
      raw_payload: {
        from: 'billing@vendor.com',
        subject: 'Payment successful',
        body: 'Payment completed.',
      },
      received_at: '2026-03-31T05:00:00.000Z',
    },
  ];

  const outputs = runPipelineOutputs(records);
  const feed = buildFeed(outputs);
  const { outputByEntityId, timingBandByEntityId } = buildOutputMaps(outputs);
  const allFeedItems = [...feed.now, ...feed.today, ...feed.worth_knowing];
  const feedEntityIds = allFeedItems.map((item) => item.entity_id);

  test('timing to section consistency', () => {
    for (const item of feed.now) {
      assert.equal(
        timingBandByEntityId.get(item.entity_id),
        'now',
        `Expected ${item.entity_id} in feed.now to have timing_band "now"`,
      );
    }

    for (const item of feed.today) {
      assert.equal(
        timingBandByEntityId.get(item.entity_id),
        'today',
        `Expected ${item.entity_id} in feed.today to have timing_band "today"`,
      );
    }
  });

  test('no hidden or suppressed items appear', () => {
    for (const output of outputs) {
      if (output.suppressed) {
        assert.ok(
          !feedEntityIds.includes(output.entity.id),
          `Suppressed entity ${output.entity.id} appeared in feed`,
        );
      }

      if (output.attention_item?.timing_band === 'hidden') {
        assert.ok(
          !feedEntityIds.includes(output.entity.id),
          `Hidden entity ${output.entity.id} appeared in feed`,
        );
      }
    }
  });

  test('lifecycle enforcement excludes resolved entities', () => {
    for (const output of outputs) {
      if (output.entity.lifecycle_state === 'resolved') {
        assert.ok(
          !feedEntityIds.includes(output.entity.id),
          `Resolved entity ${output.entity.id} appeared in feed`,
        );
      }
    }
  });

  test('no duplicates across sections', () => {
    assert.equal(
      new Set(feedEntityIds).size,
      feedEntityIds.length,
      `Duplicate entity ids found across feed sections: ${feedEntityIds.join(', ')}`,
    );
  });

  test('no priority inversion for now items', () => {
    const nowEntityIds = outputs
      .filter((output) => output.attention_item?.timing_band === 'now' && !output.suppressed)
      .map((output) => output.entity.id);

    for (const entityId of nowEntityIds) {
      assert.ok(
        feed.now.some((item) => item.entity_id === entityId),
        `Entity ${entityId} with timing_band "now" did not appear in feed.now`,
      );
      assert.ok(
        !feed.today.some((item) => item.entity_id === entityId),
        `Entity ${entityId} with timing_band "now" appeared in feed.today`,
      );
      assert.ok(
        !feed.worth_knowing.some((item) => item.entity_id === entityId),
        `Entity ${entityId} with timing_band "now" appeared in feed.worth_knowing`,
      );
    }
  });

  test('completeness for visible outputs', () => {
    for (const output of outputs) {
      const isVisibleOutput =
        output.suppressed === false &&
        output.attention_item !== null &&
        output.attention_item.timing_band !== 'hidden';

      if (!isVisibleOutput) {
        continue;
      }

      const appearances = allFeedItems.filter((item) => item.entity_id === output.entity.id).length;

      assert.equal(
        appearances,
        1,
        `Visible entity ${output.entity.id} should appear exactly once in feed, found ${appearances}`,
      );
      assert.ok(
        outputByEntityId.has(output.entity.id),
        `Missing PipelineOutput mapping for entity ${output.entity.id}`,
      );
    }
  });
});

describe('real-world scenarios', () => {
  test('thread evolution over time converges to one visible reply item', () => {
    const records: SourceRecord[] = [
      createRecord(
        'evo-1',
        'thread-evolution',
        '2026-03-20T09:00:00.000Z',
        {
          from: 'alex@example.com',
          subject: 'Initial note',
          body: 'Sharing context for next week.',
        },
        'user-evolution',
      ),
      createRecord(
        'evo-2',
        'thread-evolution',
        '2026-03-27T09:00:00.000Z',
        {
          from: 'alex@example.com',
          subject: 'Following up',
          body: 'Can you review this and let me know?',
        },
        'user-evolution',
      ),
      createRecord(
        'evo-3',
        'thread-evolution',
        '2026-03-31T08:00:00.000Z',
        {
          from: 'alex@example.com',
          subject: 'Need response today',
          body: 'Please reply when you can.',
        },
        'user-evolution',
      ),
    ];

    const feed = runPipeline(records);

    assert.deepEqual(summarizeFeed(feed), {
      now: [],
      today: ['thread-evolution'],
      worth_knowing: [],
    });
    assert.equal(
      findVisibleItem(feed, 'thread-evolution')?.primary_action,
      'reply',
      'Expected evolving thread to end as one reply item',
    );
  });

  test('duplicate reminder spam in one thread stays collapsed to one card', () => {
    const records = Array.from({ length: 8 }, (_, index) =>
      createRecord(
        `reminder-${index + 1}`,
        'thread-reminder-spam',
        `2026-03-${String(index + 20).padStart(2, '0')}T09:00:00.000Z`,
        {
          from: 'billing@vendor.com',
          subject: `Reminder ${index + 1}`,
          body: 'Amount due 2026-04-05T00:00:00.000Z.',
        },
        'user-reminder',
      ),
    );

    const feed = runPipeline(records);

    assert.deepEqual(summarizeFeed(feed), {
      now: [],
      today: [],
      worth_knowing: ['thread-reminder-spam'],
    });
    assert.equal(
      collectFeedEntityIds(feed).length,
      1,
      'Expected repeated reminders in one thread to collapse to one visible item',
    );
  });

  test('mixed signal thread preserves the strongest actionable signal', () => {
    const records: SourceRecord[] = [
      createRecord(
        'mixed-1',
        'thread-mixed-signal',
        '2026-03-28T09:00:00.000Z',
        {
          from: 'no-reply@updates.example.com',
          subject: 'Weekly digest',
          body: 'Your roundup for the week.',
        },
        'user-mixed',
      ),
      createRecord(
        'mixed-2',
        'thread-mixed-signal',
        '2026-03-31T07:00:00.000Z',
        {
          from: 'morgan@example.com',
          subject: 'Need your view',
          body: 'Could you reply by 2026-04-02T00:00:00.000Z?',
        },
        'user-mixed',
      ),
    ];

    const feed = runPipeline(records);
    const item = findVisibleItem(feed, 'thread-mixed-signal');

    assert.deepEqual(summarizeFeed(feed), {
      now: [],
      today: ['thread-mixed-signal'],
      worth_knowing: [],
    });
    assert.equal(item?.primary_action, 'reply');
    assert.equal(item?.title, 'Reply needed');
  });

  test('old email resurfaces based on due date instead of age', () => {
    const records: SourceRecord[] = [
      createRecord(
        'old-1',
        'thread-old-resurface',
        '2026-02-01T09:00:00.000Z',
        {
          from: 'billing@vendor.com',
          subject: 'Renewal notice',
          body: 'Your renewal is due 2026-04-01T00:00:00.000Z.',
        },
        'user-old',
      ),
    ];

    const feed = runPipeline(records);

    assert.deepEqual(summarizeFeed(feed), {
      now: [],
      today: ['thread-old-resurface'],
      worth_knowing: [],
    });
    assert.equal(
      findVisibleItem(feed, 'thread-old-resurface')?.title,
      'Due 2026-04-01T00:00:00.000Z',
      'Expected old email to resurface because of due date proximity',
    );
  });
});

describe('adversarial inputs', () => {
  test('garbage payloads do not crash and stay out of the feed', () => {
    const records: SourceRecord[] = [
      createRecord(
        'garbage-1',
        'thread-garbage',
        '2026-03-31T09:00:00.000Z',
        {
          from: null,
          subject: 42,
          nested: [{ ok: false }, { values: [null, 123, true] }],
          body: { unexpected: ['shape', { more: 99 }] },
        },
        'user-garbage',
      ),
    ];

    const feed = runPipeline(records);

    assert.deepEqual(summarizeFeed(feed), {
      now: [],
      today: [],
      worth_knowing: [],
    });
  });

  test('invalid date strings do not break actionable threads', () => {
    const records: SourceRecord[] = [
      createRecord(
        'invalid-date-1',
        'thread-invalid-date',
        '2026-03-31T10:00:00.000Z',
        {
          from: 'jamie@example.com',
          subject: 'Need your response',
          body: 'Please reply before 2026-13-99T99:99:99.000Z if possible.',
        },
        'user-invalid-date',
      ),
    ];

    const feed = runPipeline(records);
    const item = findVisibleItem(feed, 'thread-invalid-date');

    assert.deepEqual(summarizeFeed(feed), {
      now: [],
      today: ['thread-invalid-date'],
      worth_knowing: [],
    });
    assert.equal(item?.title, 'Reply needed');
    assert.equal(
      item?.why_this_is_here,
      'This is here because the thread appears to need a reply.',
    );
  });

  test('conflicting state and lifecycle still suppresses the entity', () => {
    const records: SourceRecord[] = [
      createRecord(
        'conflict-strong-1',
        'thread-conflict-strong',
        '2026-03-31T11:00:00.000Z',
        {
          from: 'billing@vendor.com',
          subject: 'Overdue invoice',
          body: 'Amount due 2026-03-30T00:00:00.000Z.',
        },
        'user-conflict-strong',
      ),
    ];

    const feed = runPipeline(records, CURRENT_TIME, (entity) => ({
      ...entity,
      lifecycle_state: 'resolved',
    }));

    assert.deepEqual(summarizeFeed(feed), {
      now: [],
      today: [],
      worth_knowing: [],
    });
    assert.equal(
      findVisibleItem(feed, 'thread-conflict-strong'),
      undefined,
      'Resolved lifecycle should suppress even a strong now candidate',
    );
  });

  test('large batch remains deterministic and bounded', () => {
    const overdueRecords = Array.from({ length: 20 }, (_, index) =>
      createRecord(
        `stress-now-${index}`,
        `thread-stress-now-${index}`,
        `2026-03-30T${String(index).padStart(2, '0')}:00:00.000Z`,
        {
          from: 'billing@vendor.com',
          subject: `Overdue invoice ${index}`,
          body: 'Amount due 2026-03-30T00:00:00.000Z.',
        },
        'user-stress',
      ),
    );
    const replyRecords = Array.from({ length: 20 }, (_, index) =>
      createRecord(
        `stress-today-${index}`,
        `thread-stress-today-${index}`,
        `2026-03-31T${String(index).padStart(2, '0')}:00:00.000Z`,
        {
          from: `person${index}@example.com`,
          subject: `Question ${index}`,
          body: 'Please reply when you can.',
        },
        'user-stress',
      ),
    );
    const laterRecords = Array.from({ length: 20 }, (_, index) =>
      createRecord(
        `stress-later-${index}`,
        `thread-stress-later-${index}`,
        `2026-03-29T${String(index).padStart(2, '0')}:00:00.000Z`,
        {
          from: 'billing@vendor.com',
          subject: `Invoice ${index}`,
          body: 'Amount due 2026-04-05T00:00:00.000Z.',
        },
        'user-stress',
      ),
    );
    const noiseRecords = Array.from({ length: 20 }, (_, index) =>
      createRecord(
        `stress-noise-${index}`,
        `thread-stress-noise-${index}`,
        `2026-03-28T${String(index).padStart(2, '0')}:00:00.000Z`,
        {
          from: 'no-reply@updates.example.com',
          subject: `Newsletter ${index}`,
          body: 'Weekly roundup.',
        },
        'user-stress',
      ),
    );

    const records = [...overdueRecords, ...replyRecords, ...laterRecords, ...noiseRecords];
    const first = runPipeline(records);
    const second = runPipeline(records);
    const allVisibleIds = collectFeedEntityIds(first);

    assert.equal(records.length, 80, 'Expected stress batch to include 80 records');
    assert.deepEqual(first, second, 'Expected stress batch to remain deterministic');
    assert.equal(first.now.length, 20, 'Expected all overdue invoices to land in now');
    assert.equal(first.today.length, 20, 'Expected all reply requests to land in today');
    assert.equal(
      first.worth_knowing.length,
      20,
      'Expected later invoices to land in worth_knowing',
    );
    assert.equal(
      new Set(allVisibleIds).size,
      allVisibleIds.length,
      'Expected no duplicates across visible sections in stress batch',
    );
  });

  test('messy batch remains deterministic across repeated runs', () => {
    const records: SourceRecord[] = [
      createRecord(
        'messy-1',
        'thread-messy-a',
        '2026-03-31T08:00:00.000Z',
        {
          from: 'alex@example.com',
          subject: 'Can you reply?',
          body: 'Please reply by end of day.',
        },
        'user-messy',
      ),
      createRecord(
        'messy-2',
        'thread-messy-b',
        '2026-03-31T08:05:00.000Z',
        {
          from: 'billing@vendor.com',
          subject: 'Invoice',
          body: 'Amount due 2026-04-05T00:00:00.000Z.',
        },
        'user-messy',
      ),
      createRecord(
        'messy-3',
        'thread-messy-c',
        '2026-03-31T08:10:00.000Z',
        {
          from: 'no-reply@updates.example.com',
          subject: 'Newsletter',
          body: 'Weekly roundup.',
        },
        'user-messy',
      ),
      createRecord(
        'messy-4',
        'thread-messy-d',
        '2026-03-31T08:15:00.000Z',
        {
          from: 'billing@vendor.com',
          subject: 'Overdue invoice',
          body: 'Amount due 2026-03-30T00:00:00.000Z.',
        },
        'user-messy',
      ),
      createRecord(
        'messy-5',
        'thread-messy-e',
        '2026-03-31T08:20:00.000Z',
        {
          from: 'sam@example.com',
          subject: 'Need your view',
          body: 'Could you let me know?',
        },
        'user-messy',
      ),
    ];

    const runs = Array.from({ length: 5 }, () => runPipeline(records));

    for (let index = 1; index < runs.length; index += 1) {
      assert.deepEqual(
        runs[0],
        runs[index],
        `Expected repeated run ${index + 1} to match the first feed exactly`,
      );
    }
  });
});

describe('product correctness', () => {
  test('minimal feed size under heavy noise', () => {
    const noiseRecords = Array.from({ length: 40 }, (_, index) =>
      createRecord(
        `noise-${index}`,
        `thread-product-noise-${index}`,
        `2026-03-31T${String(index % 24).padStart(2, '0')}:00:00.000Z`,
        {
          from: 'no-reply@updates.example.com',
          subject: `Newsletter ${index}`,
          body: 'Weekly roundup.',
        },
        'user-product',
      ),
    );
    const urgentRecord = createRecord(
      'noise-urgent',
      'thread-product-urgent',
      '2026-03-31T12:00:00.000Z',
      {
        from: 'billing@vendor.com',
        subject: 'Overdue invoice',
        body: 'Amount due 2026-03-30T00:00:00.000Z.',
      },
      'user-product',
    );

    const feed = runPipeline([...noiseRecords, urgentRecord]);

    assert.deepEqual(summarizeFeed(feed), {
      now: ['thread-product-urgent'],
      today: [],
      worth_knowing: [],
    });
    assert.equal(
      collectFeedEntityIds(feed).length,
      1,
      'Expected only the urgent item to survive heavy noise',
    );
  });

  test('urgent items are isolated into now', () => {
    const records: SourceRecord[] = [
      createRecord(
        'product-urgent-1',
        'thread-product-now',
        '2026-03-31T09:00:00.000Z',
        {
          from: 'billing@vendor.com',
          subject: 'Overdue invoice',
          body: 'Amount due 2026-03-30T00:00:00.000Z.',
        },
        'user-product-urgent',
      ),
      createRecord(
        'product-urgent-2',
        'thread-product-today',
        '2026-03-31T09:05:00.000Z',
        {
          from: 'alex@example.com',
          subject: 'Need response',
          body: 'Please reply when you can.',
        },
        'user-product-urgent',
      ),
      createRecord(
        'product-urgent-3',
        'thread-product-later',
        '2026-03-31T09:10:00.000Z',
        {
          from: 'billing@vendor.com',
          subject: 'Invoice later',
          body: 'Amount due 2026-04-05T00:00:00.000Z.',
        },
        'user-product-urgent',
      ),
    ];

    const feed = runPipeline(records);

    assert.deepEqual(summarizeFeed(feed), {
      now: ['thread-product-now'],
      today: ['thread-product-today'],
      worth_knowing: ['thread-product-later'],
    });
    assert.ok(
      !feed.today.some((item) => item.entity_id === 'thread-product-now'),
      'Urgent item must not leak into today',
    );
    assert.ok(
      !feed.worth_knowing.some((item) => item.entity_id === 'thread-product-now'),
      'Urgent item must not leak into worth_knowing',
    );
  });

  test('awareness does not pollute main sections', () => {
    const records: SourceRecord[] = [
      createRecord(
        'awareness-1',
        'thread-awareness-1',
        '2026-03-31T10:00:00.000Z',
        {
          from: 'no-reply@updates.example.com',
          subject: 'Newsletter',
          body: 'Weekly roundup.',
        },
        'user-awareness',
      ),
      createRecord(
        'awareness-2',
        'thread-awareness-2',
        '2026-03-31T10:05:00.000Z',
        {
          from: 'no-reply@updates.example.com',
          subject: 'Product digest',
          body: 'Latest updates.',
        },
        'user-awareness',
      ),
      createRecord(
        'awareness-3',
        'thread-awareness-3',
        '2026-03-31T10:10:00.000Z',
        {
          from: 'billing@vendor.com',
          subject: 'Payment successful',
          body: 'Payment completed.',
        },
        'user-awareness',
      ),
    ];

    const feed = runPipeline(records);

    assert.equal(feed.now.length, 0, 'Awareness-only batch should not create now items');
    assert.equal(feed.today.length, 0, 'Awareness-only batch should not create today items');
    assert.equal(
      feed.worth_knowing.length,
      0,
      'Current pipeline suppresses awareness-only items instead of surfacing them',
    );
  });
});

describe('temporal reality', () => {
  test('obligation escalates from worth_knowing to now as time advances', () => {
    const records: SourceRecord[] = [
      createRecord(
        'temporal-1',
        'thread-temporal-escalation',
        '2026-03-20T09:00:00.000Z',
        {
          from: 'billing@vendor.com',
          subject: 'Invoice reminder',
          body: 'Amount due 2026-04-05T00:00:00.000Z.',
        },
        'user-temporal',
      ),
    ];

    const earlyFeed = runPipeline(records, '2026-03-31T00:00:00.000Z');
    const lateFeed = runPipeline(records, '2026-04-04T12:00:00.000Z');

    assert.deepEqual(summarizeFeed(earlyFeed), {
      now: [],
      today: [],
      worth_knowing: ['thread-temporal-escalation'],
    });
    assert.deepEqual(summarizeFeed(lateFeed), {
      now: ['thread-temporal-escalation'],
      today: [],
      worth_knowing: [],
    });
  });

  test('completion suppresses a previously urgent merged obligation', () => {
    const records: SourceRecord[] = [
      createRecord(
        'temporal-2',
        'thread-temporal-reminder-1',
        '2026-03-30T08:00:00.000Z',
        {
          from: 'alerts@hdfc.com',
          subject: 'Payment due reminder',
          body: 'Payment due on 2026-03-30T00:00:00.000Z. Card ending 1234.',
        },
        'user-temporal',
      ),
      createRecord(
        'temporal-3',
        'thread-temporal-reminder-2',
        '2026-03-31T08:00:00.000Z',
        {
          from: 'billing@hdfc.com',
          subject: 'Payment completed',
          body: 'Payment completed for card ending 1234.',
        },
        'user-temporal',
      ),
    ];

    const feed = runPipeline(records);

    assert.deepEqual(summarizeFeed(feed), {
      now: [],
      today: [],
      worth_knowing: [],
    });
  });

  test('old overdue email remains visible despite age', () => {
    const records: SourceRecord[] = [
      createRecord(
        'temporal-4',
        'thread-temporal-old-overdue',
        '2026-01-15T09:00:00.000Z',
        {
          from: 'billing@vendor.com',
          subject: 'Invoice overdue',
          body: 'Amount due 2026-03-30T00:00:00.000Z.',
        },
        'user-temporal',
      ),
    ];

    const feed = runPipeline(records);

    assert.deepEqual(summarizeFeed(feed), {
      now: ['thread-temporal-old-overdue'],
      today: [],
      worth_knowing: [],
    });
  });
});

describe('ambiguity reality', () => {
  test('explicit request surfaces while implicit intent stays hidden', () => {
    const records: SourceRecord[] = [
      createRecord(
        'ambiguity-1',
        'thread-ambiguity-explicit',
        '2026-03-31T09:00:00.000Z',
        {
          from: 'alex@example.com',
          subject: 'Need your input',
          body: 'Could you reply by tomorrow?',
        },
        'user-ambiguity',
      ),
      createRecord(
        'ambiguity-2',
        'thread-ambiguity-implicit',
        '2026-03-31T09:05:00.000Z',
        {
          from: 'alex@example.com',
          subject: 'Sharing this in case useful',
          body: 'Thought you would want to see this.',
        },
        'user-ambiguity',
      ),
    ];

    const feed = runPipeline(records);

    assert.deepEqual(summarizeFeed(feed), {
      now: [],
      today: ['thread-ambiguity-explicit'],
      worth_knowing: [],
    });
    assert.equal(findVisibleItem(feed, 'thread-ambiguity-implicit'), undefined);
  });

  test('ambiguous human language without a clear ask remains suppressed', () => {
    const records: SourceRecord[] = [
      createRecord(
        'ambiguity-3',
        'thread-ambiguity-vague',
        '2026-03-31T10:00:00.000Z',
        {
          from: 'jamie@example.com',
          subject: 'Maybe worth discussing',
          body: 'Lets catch up sometime next week.',
        },
        'user-ambiguity',
      ),
    ];

    const feed = runPipeline(records);

    assert.deepEqual(summarizeFeed(feed), {
      now: [],
      today: [],
      worth_knowing: [],
    });
  });

  test('incomplete financial reminder without due date does not create a false main-surface item', () => {
    const records: SourceRecord[] = [
      createRecord(
        'ambiguity-4',
        'thread-ambiguity-financial',
        '2026-03-31T11:00:00.000Z',
        {
          from: 'billing@vendor.com',
          subject: 'Payment reminder',
          body: 'Your statement is ready.',
        },
        'user-ambiguity',
      ),
    ];

    const feed = runPipeline(records);

    assert.deepEqual(summarizeFeed(feed), {
      now: [],
      today: [],
      worth_knowing: [],
    });
  });
});

describe('fragmentation reality', () => {
  test('cross-thread reminder campaign collapses into one visible entity', () => {
    const records: SourceRecord[] = [
      createRecord(
        'fragment-1',
        'thread-fragment-1',
        '2026-03-29T08:00:00.000Z',
        {
          from: 'alerts@hdfc.com',
          subject: 'Bill due reminder',
          body: 'Bill due on 2026-04-05. Card ending 1234.',
        },
        'user-fragment',
      ),
      createRecord(
        'fragment-2',
        'thread-fragment-2',
        '2026-03-31T08:00:00.000Z',
        {
          from: 'billing@hdfc.com',
          subject: 'Payment due reminder',
          body: 'Payment due on 2026-04-05. Card ending 1234.',
        },
        'user-fragment',
      ),
      createRecord(
        'fragment-3',
        'thread-fragment-3',
        '2026-04-02T08:00:00.000Z',
        {
          from: 'reminders@hdfc.com',
          subject: 'Statement due reminder',
          body: 'Statement due on 2026-04-05. Card ending 1234.',
        },
        'user-fragment',
      ),
    ];

    const feed = runPipeline(records);
    const visibleIds = collectFeedEntityIds(feed);

    assert.equal(visibleIds.length, 1);
    assert.equal(new Set(visibleIds).size, 1);
  });

  test('same domain but distinct obligations remain separate when due dates differ', () => {
    const records: SourceRecord[] = [
      createRecord(
        'fragment-4',
        'thread-fragment-a',
        '2026-03-31T09:00:00.000Z',
        {
          from: 'alerts@hdfc.com',
          subject: 'Bill due reminder',
          body: 'Bill due on 2026-04-02. Card ending 1111.',
        },
        'user-fragment',
      ),
      createRecord(
        'fragment-5',
        'thread-fragment-b',
        '2026-03-31T09:05:00.000Z',
        {
          from: 'alerts@hdfc.com',
          subject: 'Bill due reminder',
          body: 'Bill due on 2026-04-05. Card ending 2222.',
        },
        'user-fragment',
      ),
    ];

    const feed = runPipeline(records);
    const visibleIds = collectFeedEntityIds(feed);

    assert.equal(visibleIds.length, 2);
    assert.equal(new Set(visibleIds).size, 2);
    assert.equal(feed.now.length, 0);
    assert.equal(feed.today.length, 1);
    assert.equal(feed.worth_knowing.length, 1);
  });

  test('multi-email same-thread lifecycle ends in a single resolved entity', () => {
    const records: SourceRecord[] = [
      createRecord(
        'fragment-6',
        'thread-fragment-lifecycle',
        '2026-03-29T08:00:00.000Z',
        {
          from: 'billing@vendor.com',
          subject: 'Invoice reminder',
          body: 'Amount due 2026-03-30T00:00:00.000Z.',
        },
        'user-fragment',
      ),
      createRecord(
        'fragment-7',
        'thread-fragment-lifecycle',
        '2026-03-31T08:00:00.000Z',
        {
          from: 'billing@vendor.com',
          subject: 'Payment completed',
          body: 'Your payment is completed.',
        },
        'user-fragment',
      ),
    ];

    const feed = runPipeline(records);

    assert.deepEqual(summarizeFeed(feed), {
      now: [],
      today: [],
      worth_knowing: [],
    });
  });
});

describe('noise vs signal', () => {
  test('heavy noise does not leak into main sections', () => {
    const noiseRecords = Array.from({ length: 60 }, (_, index) =>
      createRecord(
        `noise-signal-${index}`,
        `thread-noise-signal-${index}`,
        `2026-03-31T${String(index % 24).padStart(2, '0')}:00:00.000Z`,
        {
          from: 'no-reply@updates.example.com',
          subject: `Newsletter ${index}`,
          body: 'Weekly roundup.',
        },
        'user-noise-signal',
      ),
    );
    const urgentRecord = createRecord(
      'noise-signal-urgent',
      'thread-noise-signal-urgent',
      '2026-03-31T13:00:00.000Z',
      {
        from: 'billing@vendor.com',
        subject: 'Overdue invoice',
        body: 'Amount due 2026-03-30T00:00:00.000Z.',
      },
      'user-noise-signal',
    );
    const todayRecord = createRecord(
      'noise-signal-today',
      'thread-noise-signal-today',
      '2026-03-31T14:00:00.000Z',
      {
        from: 'alex@example.com',
        subject: 'Need your response',
        body: 'Please reply when you can.',
      },
      'user-noise-signal',
    );

    const feed = runPipeline([...noiseRecords, urgentRecord, todayRecord]);

    assert.deepEqual(summarizeFeed(feed), {
      now: ['thread-noise-signal-urgent'],
      today: ['thread-noise-signal-today'],
      worth_knowing: [],
    });
  });

  test('urgent items stay visible even inside mixed noisy distribution', () => {
    const records: SourceRecord[] = [
      createRecord(
        'noise-mixed-1',
        'thread-noise-mixed-1',
        '2026-03-31T08:00:00.000Z',
        {
          from: 'no-reply@updates.example.com',
          subject: 'Newsletter',
          body: 'Weekly roundup.',
        },
        'user-noise-signal',
      ),
      createRecord(
        'noise-mixed-2',
        'thread-noise-mixed-2',
        '2026-03-31T08:05:00.000Z',
        {
          from: 'billing@vendor.com',
          subject: 'Overdue invoice',
          body: 'Amount due 2026-03-30T00:00:00.000Z.',
        },
        'user-noise-signal',
      ),
      createRecord(
        'noise-mixed-3',
        'thread-noise-mixed-3',
        '2026-03-31T08:10:00.000Z',
        {
          from: 'billing@vendor.com',
          subject: 'Invoice reminder',
          body: 'Amount due 2026-04-05T00:00:00.000Z.',
        },
        'user-noise-signal',
      ),
    ];

    const feed = runPipeline(records);

    assert.ok(
      feed.now.some((item) => item.entity_id === 'thread-noise-mixed-2'),
      'Urgent item should remain visible in now',
    );
    assert.ok(
      !feed.today.some((item) => item.entity_id === 'thread-noise-mixed-1'),
      'Noise item must not leak into today',
    );
  });
});

describe('distribution stress', () => {
  test('mixed priority distribution keeps sections separated with no duplicates', () => {
    const overdueRecords = Array.from({ length: 25 }, (_, index) =>
      createRecord(
        `dist-now-${index}`,
        `thread-dist-now-${index}`,
        `2026-03-30T${String(index % 24).padStart(2, '0')}:00:00.000Z`,
        {
          from: 'billing@vendor.com',
          subject: `Overdue invoice ${index}`,
          body: 'Amount due 2026-03-30T00:00:00.000Z.',
        },
        'user-distribution',
      ),
    );
    const todayRecords = Array.from({ length: 20 }, (_, index) =>
      createRecord(
        `dist-today-${index}`,
        `thread-dist-today-${index}`,
        `2026-03-31T${String(index % 24).padStart(2, '0')}:00:00.000Z`,
        {
          from: `person${index}@example.com`,
          subject: `Need response ${index}`,
          body: 'Please reply when you can.',
        },
        'user-distribution',
      ),
    );
    const laterRecords = Array.from({ length: 15 }, (_, index) =>
      createRecord(
        `dist-later-${index}`,
        `thread-dist-later-${index}`,
        `2026-03-29T${String(index % 24).padStart(2, '0')}:00:00.000Z`,
        {
          from: 'billing@vendor.com',
          subject: `Invoice later ${index}`,
          body: 'Amount due 2026-04-05T00:00:00.000Z.',
        },
        'user-distribution',
      ),
    );
    const noiseRecords = Array.from({ length: 20 }, (_, index) =>
      createRecord(
        `dist-noise-${index}`,
        `thread-dist-noise-${index}`,
        `2026-03-28T${String(index % 24).padStart(2, '0')}:00:00.000Z`,
        {
          from: 'no-reply@updates.example.com',
          subject: `Newsletter ${index}`,
          body: 'Weekly roundup.',
        },
        'user-distribution',
      ),
    );

    const feed = runPipeline([...overdueRecords, ...todayRecords, ...laterRecords, ...noiseRecords]);
    const visibleIds = collectFeedEntityIds(feed);

    assert.equal(feed.now.length, 25);
    assert.equal(feed.today.length, 20);
    assert.equal(feed.worth_knowing.length, 15);
    assert.equal(new Set(visibleIds).size, visibleIds.length);
  });

  test('shuffled input yields the same visible feed', () => {
    const records: SourceRecord[] = [
      createRecord(
        'shuffle-1',
        'thread-shuffle-1',
        '2026-03-31T08:00:00.000Z',
        {
          from: 'billing@vendor.com',
          subject: 'Overdue invoice',
          body: 'Amount due 2026-03-30T00:00:00.000Z.',
        },
        'user-shuffle',
      ),
      createRecord(
        'shuffle-2',
        'thread-shuffle-2',
        '2026-03-31T08:05:00.000Z',
        {
          from: 'alex@example.com',
          subject: 'Need response',
          body: 'Please reply when you can.',
        },
        'user-shuffle',
      ),
      createRecord(
        'shuffle-3',
        'thread-shuffle-3',
        '2026-03-31T08:10:00.000Z',
        {
          from: 'billing@vendor.com',
          subject: 'Invoice reminder',
          body: 'Amount due 2026-04-05T00:00:00.000Z.',
        },
        'user-shuffle',
      ),
      createRecord(
        'shuffle-4',
        'thread-shuffle-4',
        '2026-03-31T08:15:00.000Z',
        {
          from: 'no-reply@updates.example.com',
          subject: 'Newsletter',
          body: 'Weekly roundup.',
        },
        'user-shuffle',
      ),
    ];

    const orderedFeed = runPipeline(records);
    const reorderedFeed = runPipeline(reorderRecordsDeterministically(records));

    assert.deepEqual(sortFeed(orderedFeed), sortFeed(reorderedFeed));
  });
});

describe('consistency guarantees', () => {
  test('repeated execution remains deterministic for fragmented mixed input', () => {
    const records: SourceRecord[] = [
      createRecord(
        'consistency-1',
        'thread-consistency-1',
        '2026-03-31T08:00:00.000Z',
        {
          from: 'alerts@hdfc.com',
          subject: 'Bill due reminder',
          body: 'Bill due on 2026-04-05. Card ending 1234.',
        },
        'user-consistency',
      ),
      createRecord(
        'consistency-2',
        'thread-consistency-2',
        '2026-03-31T08:05:00.000Z',
        {
          from: 'billing@hdfc.com',
          subject: 'Payment due reminder',
          body: 'Payment due on 2026-04-05. Card ending 1234.',
        },
        'user-consistency',
      ),
      createRecord(
        'consistency-3',
        'thread-consistency-3',
        '2026-03-31T08:10:00.000Z',
        {
          from: 'alex@example.com',
          subject: 'Need your response',
          body: 'Please reply when you can.',
        },
        'user-consistency',
      ),
      createRecord(
        'consistency-4',
        'thread-consistency-4',
        '2026-03-31T08:15:00.000Z',
        {
          from: 'no-reply@updates.example.com',
          subject: 'Newsletter',
          body: 'Weekly roundup.',
        },
        'user-consistency',
      ),
    ];

    const runs = Array.from({ length: 5 }, () => runPipeline(records));

    for (let index = 1; index < runs.length; index += 1) {
      assert.deepEqual(runs[0], runs[index]);
    }
  });

  test('visible items remain unique and urgent items remain visible', () => {
    const records: SourceRecord[] = [
      createRecord(
        'consistency-5',
        'thread-consistency-now',
        '2026-03-31T09:00:00.000Z',
        {
          from: 'billing@vendor.com',
          subject: 'Overdue invoice',
          body: 'Amount due 2026-03-30T00:00:00.000Z.',
        },
        'user-consistency',
      ),
      createRecord(
        'consistency-6',
        'thread-consistency-today',
        '2026-03-31T09:05:00.000Z',
        {
          from: 'alex@example.com',
          subject: 'Need response',
          body: 'Please reply when you can.',
        },
        'user-consistency',
      ),
      createRecord(
        'consistency-7',
        'thread-consistency-later',
        '2026-03-31T09:10:00.000Z',
        {
          from: 'billing@vendor.com',
          subject: 'Invoice reminder',
          body: 'Amount due 2026-04-05T00:00:00.000Z.',
        },
        'user-consistency',
      ),
    ];

    const outputs = runPipelineOutputs(records);
    const feed = buildFeed(outputs);
    const { timingBandByEntityId } = buildOutputMaps(outputs);
    const visibleIds = collectFeedEntityIds(feed);

    assert.equal(new Set(visibleIds).size, visibleIds.length);
    for (const item of feed.now) {
      assert.equal(
        timingBandByEntityId.get(item.entity_id),
        'now',
        `Expected urgent entity ${item.entity_id} to remain visible in now`,
      );
    }
    assert.ok(feed.now.length > 0, 'Expected at least one urgent item in now');
  });
});
