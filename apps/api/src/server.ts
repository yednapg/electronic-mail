import cors from 'cors';
import express from 'express';
import type { FeedResponse, SourceRecord } from '@electronic-mail/types';

import { assignTiming } from './assignTiming';
import { buildFeed } from './buildFeed';
import { classifyEntity } from './classifyEntity';
import { testDatabaseConnection } from './db';
import { env } from './env';
import { groupSourceRecordsToEntities } from './groupSourceRecordsToEntities';

const app = express();
const FEED_CURRENT_TIME = '2026-03-31T00:00:00.000Z';
const MOCK_SOURCE_RECORDS: SourceRecord[] = [
  {
    id: 'feed-now-1',
    user_id: 'demo-user',
    source: 'gmail',
    thread_id: 'thread-feed-now',
    raw_payload: {
      from: 'billing@vendor.com',
      subject: 'Overdue invoice',
      body: 'Amount due 2026-03-30T00:00:00.000Z.',
    },
    received_at: '2026-03-31T08:00:00.000Z',
  },
  {
    id: 'feed-today-1',
    user_id: 'demo-user',
    source: 'gmail',
    thread_id: 'thread-feed-today',
    raw_payload: {
      from: 'alex@example.com',
      subject: 'Need your response',
      body: 'Please reply when you can.',
    },
    received_at: '2026-03-31T09:00:00.000Z',
  },
  {
    id: 'feed-later-1',
    user_id: 'demo-user',
    source: 'gmail',
    thread_id: 'thread-feed-later',
    raw_payload: {
      from: 'billing@vendor.com',
      subject: 'Invoice reminder',
      body: 'Amount due 2026-04-05T00:00:00.000Z.',
    },
    received_at: '2026-03-31T10:00:00.000Z',
  },
  {
    id: 'feed-hidden-1',
    user_id: 'demo-user',
    source: 'gmail',
    thread_id: 'thread-feed-hidden',
    raw_payload: {
      from: 'no-reply@updates.example.com',
      subject: 'Weekly newsletter',
      body: 'This is your weekly roundup.',
    },
    received_at: '2026-03-31T11:00:00.000Z',
  },
];

type HealthResponse = {
  status: 'ok';
};

app.use(cors({ origin: env.corsOrigin }));
app.use(express.json());

app.get('/health', (_request, response) => {
  const payload: HealthResponse = { status: 'ok' };

  response.json(payload);
});

app.get('/feed', (_request, response) => {
  const payload = buildMockFeed();

  response.json(payload);
});

function buildMockFeed(): FeedResponse {
  const outputs = groupSourceRecordsToEntities(MOCK_SOURCE_RECORDS).map((entity) => {
    const classified = classifyEntity(entity);

    return assignTiming(entity, classified, FEED_CURRENT_TIME);
  });

  return buildFeed(outputs);
}

async function startServer(): Promise<void> {
  await testDatabaseConnection();

  app.listen(env.port, () => {
    console.log(`API listening on http://localhost:${env.port}`);
  });
}

startServer().catch((error: unknown) => {
  console.error('Failed to start API', error);
  process.exit(1);
});
