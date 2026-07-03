import assert from 'node:assert/strict';
import test from 'node:test';

import type { FirstRunImportJobResponse } from './types';
import { formatFirstRunImportStatus, isFirstRunImportReady } from './first-run-import';

test('first-run readiness waits for the 30-day hot window before app entry', () => {
  const job = makeFirstRunJob({
    inbox_ready_at: '2026-06-20T08:00:00Z',
    first_groups_ready_at: '2026-06-20T08:00:03Z',
    dashboard_ready_at: null,
  });

  assert.equal(isFirstRunImportReady(job), false);
  assert.equal(formatFirstRunImportStatus(job), 'Preparing your 30-day inbox...');
  assert.equal(isFirstRunImportReady({ ...job, hot_window_ready_at: '2026-06-20T08:00:45Z' }), true);
  assert.equal(formatFirstRunImportStatus({ ...job, hot_window_ready_at: '2026-06-20T08:00:45Z' }), 'Inbox is ready. Opening the app...');
});

test('first-run status copy is inbox-first', () => {
  assert.equal(formatFirstRunImportStatus(makeFirstRunJob({ stage: 'queued' })), 'Starting inbox setup...');
  assert.equal(formatFirstRunImportStatus(makeFirstRunJob({ inbox_ready_at: '2026-06-20T08:00:00Z' })), 'Writing useful titles...');
  assert.equal(formatFirstRunImportStatus(makeFirstRunJob({ stage: 'inbox_projection' })), 'Preparing your 30-day inbox...');
  assert.equal(formatFirstRunImportStatus(makeFirstRunJob({ stage: 'dashboard_fast_feed' })), 'Preparing your priority inbox...');
  assert.equal(formatFirstRunImportStatus(makeFirstRunJob({ stage: 'dashboard_filtering' })), 'Finishing inbox priorities...');
  assert.equal(formatFirstRunImportStatus(makeFirstRunJob({ stage: 'ready' })), 'Inbox is ready...');
});

function makeFirstRunJob(overrides: Partial<FirstRunImportJobResponse> = {}): FirstRunImportJobResponse {
  return {
    id: 'job-1',
    user_id: 'user-1',
    status: 'running',
    stage: 'queued',
    fetched_count: 0,
    total_count: null,
    thread_count: 0,
    dashboard_item_count: 0,
    inbox_ready_at: null,
    first_groups_ready_at: null,
    dashboard_ready_at: null,
    canonical_dashboard_ready_at: null,
    hot_window_ready_at: null,
    quality_status: 'pending',
    quality_error: null,
    full_import_started_at: null,
    full_import_completed_at: null,
    error_message: null,
    created_at: '2026-06-20T08:00:00Z',
    started_at: null,
    completed_at: null,
    updated_at: '2026-06-20T08:00:00Z',
    ...overrides,
  };
}
