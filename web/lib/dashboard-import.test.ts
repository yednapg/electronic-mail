import test from 'node:test';
import assert from 'node:assert/strict';

import {
  DashboardImportError,
  createDashboardImportJob,
  formatDashboardImportStatus,
  waitForDashboardImportJob,
} from './dashboard-import';

function response(body: object, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
    },
    ...init,
  });
}

function job(status: 'queued' | 'running' | 'succeeded' | 'failed', errorMessage: string | null = null) {
  return {
    id: 'job-1',
    user_id: 'local-user',
    status,
    stage: status === 'queued' ? 'queued' : status === 'running' ? 'gmail_fetching' : status === 'succeeded' ? 'completed' : 'failed',
    imported_count: status === 'succeeded' ? 11 : status === 'running' ? 4 : 0,
    total_count: 11,
    source_records: status === 'succeeded' ? 11 : 0,
    changed_entities: status === 'succeeded' ? 4 : 0,
    refreshed_entities: status === 'succeeded' ? 4 : 0,
    result_status: status === 'succeeded' ? 'ready' : status === 'failed' ? 'failed' : null,
    error_message: errorMessage,
    created_at: '2026-05-08T00:00:00+00:00',
    started_at: status === 'queued' ? null : '2026-05-08T00:00:01+00:00',
    completed_at: status === 'succeeded' || status === 'failed' ? '2026-05-08T00:00:02+00:00' : null,
    updated_at: '2026-05-08T00:00:02+00:00',
  };
}

test('dashboard import job starts through the local proxy route', async () => {
  const calls: string[] = [];
  const fetcher = ((input: RequestInfo | URL) => {
    calls.push(String(input));
    return Promise.resolve(response(job('queued'), { status: 202 }));
  }) as typeof fetch;

  const created = await createDashboardImportJob(fetcher);

  assert.equal(created.id, 'job-1');
  assert.equal(created.status, 'queued');
  assert.deepEqual(calls, ['/api/dashboard/import-jobs']);
});

test('dashboard import polling waits until the backend job succeeds', async () => {
  const calls: string[] = [];
  const updates: string[] = [];
  const responses = [
    response(job('queued'), { status: 202 }),
    response(job('running')),
    response(job('succeeded')),
  ];
  const fetcher = ((input: RequestInfo | URL) => {
    calls.push(String(input));
    const next = responses.shift();
    if (next === undefined) {
      throw new Error('Unexpected fetch');
    }
    return Promise.resolve(next);
  }) as typeof fetch;

  const completed = await waitForDashboardImportJob({
    fetcher,
    timeoutMs: 1000,
    pollMs: 0,
    onUpdate: (updatedJob) => updates.push(updatedJob.stage),
  });

  assert.equal(completed.status, 'succeeded');
  assert.equal(completed.source_records, 11);
  assert.deepEqual(updates, ['queued', 'gmail_fetching', 'completed']);
  assert.deepEqual(calls, [
    '/api/dashboard/import-jobs',
    '/api/dashboard/import-jobs/job-1',
    '/api/dashboard/import-jobs/job-1',
  ]);
});

test('dashboard import status formats backend-owned progress stages', () => {
  const running = job('running');

  assert.equal(formatDashboardImportStatus(running), 'Fetching Gmail threads 4/11...');
  assert.equal(
    formatDashboardImportStatus({ ...running, stage: 'source_summary', source_records: 256, imported_count: 256 }),
    'Summarizing email evidence for 256 items...',
  );
  assert.equal(
    formatDashboardImportStatus({
      ...running,
      stage: 'source_summary',
      source_records: 256,
      imported_count: 256,
      stage_started_at: '2000-01-01T00:00:01+00:00',
    }),
    'Still summarizing email evidence for 256 items...',
  );
  assert.equal(
    formatDashboardImportStatus({ ...running, stage: 'memory_hydration', source_records: 256, imported_count: 256 }),
    'Grouping related emails into work for 256 items...',
  );
  assert.equal(
    formatDashboardImportStatus({ ...running, stage: 'ai_refresh', changed_entities: 103 }),
    'Finding current state and next move for 103 items...',
  );
  assert.equal(
    formatDashboardImportStatus({ ...running, stage: 'feed_build', changed_entities: 103, refreshed_entities: 110 }),
    'Preparing refreshed dashboard items for 110 items...',
  );
  assert.equal(formatDashboardImportStatus(job('succeeded')), 'Dashboard is ready.');
});

test('dashboard import polling surfaces backend failure state', async () => {
  const responses = [
    response(job('queued'), { status: 202 }),
    response(job('failed', 'sync exploded')),
  ];
  const fetcher = (() => {
    const next = responses.shift();
    if (next === undefined) {
      throw new Error('Unexpected fetch');
    }
    return Promise.resolve(next);
  }) as typeof fetch;

  await assert.rejects(
    () => waitForDashboardImportJob({ fetcher, timeoutMs: 1000, pollMs: 0 }),
    (error) => error instanceof DashboardImportError && error.message === 'sync exploded',
  );
});

test('dashboard import polling times out without pretending dashboard is ready', async () => {
  const fetcher = (() => Promise.resolve(response(job('queued'), { status: 202 }))) as typeof fetch;

  await assert.rejects(
    () => waitForDashboardImportJob({ fetcher, timeoutMs: -1, pollMs: 0 }),
    (error) => error instanceof DashboardImportError && error.message === 'Dashboard import job timed out.',
  );
});

test('dashboard import polling can wait without a live-mode timeout', async () => {
  const responses = [
    response(job('queued'), { status: 202 }),
    response(job('running')),
    response(job('succeeded')),
  ];
  const fetcher = (() => {
    const next = responses.shift();
    if (next === undefined) {
      throw new Error('Unexpected fetch');
    }
    return Promise.resolve(next);
  }) as typeof fetch;

  const completed = await waitForDashboardImportJob({ fetcher, timeoutMs: null, pollMs: 0 });

  assert.equal(completed.status, 'succeeded');
});
