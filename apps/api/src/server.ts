import cors from 'cors';
import express from 'express';
import type { FeedResponse, SourceRecord } from '@decision-pipeline/types';

import { testDatabaseConnection } from './db';
import { env } from './env';
import {
  fetchGoogleSourceRecords,
  getGoogleAuthUrl,
  handleGoogleCallback,
  hasStoredGoogleTokens,
  isGoogleConfigured,
} from './integrations/google';
import { buildFeedFromEntities, hydratePersistentMemory } from './memoryPipeline';

const app = express();

type HealthResponse = {
  status: 'ok';
};

app.use(cors({ origin: env.corsOrigin }));
app.use(express.json());

app.get('/health', (_request, response) => {
  const payload: HealthResponse = { status: 'ok' };

  response.json(payload);
});

app.get('/auth/google', (_request, response) => {
  if (!isGoogleConfigured()) {
    response.status(500).send('Google OAuth is not configured in apps/api/.env');
    return;
  }

  response.redirect(getGoogleAuthUrl());
});

app.get('/auth/google/callback', async (request, response) => {
  const code = typeof request.query.code === 'string' ? request.query.code : null;

  if (code === null) {
    response.status(400).send('Missing OAuth code');
    return;
  }

  try {
    await handleGoogleCallback(code);
    response.redirect(`${env.corsOrigin}/dashboard`);
  } catch (error) {
    console.error('Failed to complete Google OAuth callback', error);
    response.status(500).send('Google OAuth callback failed');
  }
});

app.get('/feed', async (_request, response) => {
  const payload = await buildPipelineFeed();

  response.json(payload);
});

async function buildPipelineFeed(): Promise<FeedResponse> {
  let sourceRecords: SourceRecord[] = [];

  if (isGoogleConfigured() && hasStoredGoogleTokens()) {
    sourceRecords = await fetchGoogleSourceRecords();
  }

  const currentTime = new Date().toISOString();
  await hydratePersistentMemory(sourceRecords);

  return buildFeedFromEntities(currentTime);
}

function isSameLocalDay(left: Date, right: Date): boolean {
  return (
    left.getFullYear() === right.getFullYear() &&
    left.getMonth() === right.getMonth() &&
    left.getDate() === right.getDate()
  );
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
