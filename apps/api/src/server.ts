import cors from 'cors';
import express from 'express';

import type { HealthResponse } from '@decision-pipeline/types';

import { testDatabaseConnection } from './db';
import { env } from './env';

const app = express();

app.use(cors({ origin: env.corsOrigin }));
app.use(express.json());

app.get('/health', (_request, response) => {
  const payload: HealthResponse = { status: 'ok' };

  response.json(payload);
});

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
