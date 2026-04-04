import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import type { FeedResponse, SourceRecord } from '@decision-pipeline/types';

import { createPrismaClient } from '../db';
import { persistSourceRecords } from '../integrations/google';
import { buildFeedFromEntities, hydratePersistentMemory } from '../memoryPipeline';

test('persistent memory collapses repeated GitHub reminders into one entity', async () => {
  const harness = await createTestHarness();

  try {
    const records = Array.from({ length: 5 }, (_, index) =>
      createSourceRecord({
        id: `github-${index + 1}`,
        threadId: 'thread-github-discount',
        sender: 'GitHub <notifications@github.com>',
        subject: 'GitHub Pro discount expires April 6',
        body: 'Reminder: your GitHub Pro discount expires April 6.',
        timestamp: `2026-04-0${index + 1}T09:00:00.000Z`,
      }),
    );

    await persistSourceRecords(records, harness.client);
    await hydratePersistentMemory(records, harness.client);

    assert.equal(await harness.client.entity.count(), 1);
    assert.equal(await harness.client.entityState.count(), 1);

    const feed = await buildFeedFromEntities('2026-04-04T10:00:00.000Z', harness.client);

    assert.equal(countVisible(feed), 1);
  } finally {
    await harness.cleanup();
  }
});

test('YC lifecycle records collapse into one entity and keep one visible item', async () => {
  const harness = await createTestHarness();

  try {
    const records = [
      createSourceRecord({
        id: 'yc-registered',
        threadId: 'thread-yc-1',
        sender: 'YC <events@ycombinator.com>',
        subject: 'YC Startup School India registration',
        body: 'You are registered for YC Startup School India.',
        timestamp: '2026-04-01T08:00:00.000Z',
      }),
      createSourceRecord({
        id: 'yc-waitlist',
        threadId: 'thread-yc-2',
        sender: 'YC <events@ycombinator.com>',
        subject: 'YC Startup School India waitlist update',
        body: 'You are currently on the waitlist for YC Startup School India.',
        timestamp: '2026-04-02T08:00:00.000Z',
      }),
      createSourceRecord({
        id: 'yc-accepted',
        threadId: 'thread-yc-3',
        sender: 'YC <events@ycombinator.com>',
        subject: 'YC Startup School India accepted',
        body: 'You have been accepted for YC Startup School India.',
        timestamp: '2026-04-03T08:00:00.000Z',
      }),
      createSourceRecord({
        id: 'yc-rsvp',
        threadId: 'thread-yc-4',
        sender: 'YC <events@ycombinator.com>',
        subject: 'YC Startup School India RSVP confirmed',
        body: 'Your RSVP is confirmed for YC Startup School India.',
        timestamp: '2026-04-04T08:00:00.000Z',
      }),
    ];

    await persistSourceRecords(records, harness.client);
    await hydratePersistentMemory(records, harness.client);

    assert.equal(await harness.client.entity.count(), 1);

    const feed = await buildFeedFromEntities('2026-04-04T10:00:00.000Z', harness.client);

    assert.equal(countVisible(feed), 1);
  } finally {
    await harness.cleanup();
  }
});

test('feed state persists across refreshes from local db', async () => {
  const harness = await createTestHarness();

  try {
    const records = [
      createSourceRecord({
        id: 'persist-1',
        threadId: 'thread-persist',
        sender: 'GitHub <notifications@github.com>',
        subject: 'GitHub Pro discount expires April 6',
        body: 'Your GitHub Pro discount expires April 6.',
        timestamp: '2026-04-03T08:00:00.000Z',
      }),
    ];

    await persistSourceRecords(records, harness.client);
    await hydratePersistentMemory(records, harness.client);

    const firstFeed = await buildFeedFromEntities('2026-04-04T10:00:00.000Z', harness.client);

    await hydratePersistentMemory([], harness.client);

    const secondFeed = await buildFeedFromEntities('2026-04-04T10:00:00.000Z', harness.client);

    assert.deepEqual(firstFeed, secondFeed);
  } finally {
    await harness.cleanup();
  }
});

function countVisible(feed: FeedResponse): number {
  return feed.now.length + feed.today.length + feed.worth_knowing.length;
}

function createSourceRecord(input: {
  id: string;
  threadId: string;
  sender: string;
  subject: string;
  body: string;
  timestamp: string;
}): SourceRecord {
  return {
    id: input.id,
    user_id: 'local-user',
    source: 'gmail',
    thread_id: input.threadId,
    raw_payload: {
      subject: input.subject,
      body: input.body,
      from: input.sender,
      received_at: input.timestamp,
    },
    received_at: input.timestamp,
  };
}

async function createTestHarness(): Promise<{
  client: ReturnType<typeof createPrismaClient>;
  cleanup: () => Promise<void>;
}> {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'app-memory-'));
  const dbPath = path.join(tempDir, 'test.db');
  const databaseUrl = `file:${dbPath}`;
  const templateDbPath = path.resolve(process.cwd(), 'dev.db');

  fs.copyFileSync(templateDbPath, dbPath);

  const client = createPrismaClient(databaseUrl);
  await client.$connect();

  return {
    client,
    cleanup: async () => {
      await client.$disconnect();
      fs.rmSync(tempDir, { recursive: true, force: true });
    },
  };
}
