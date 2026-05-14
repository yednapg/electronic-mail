import { NextResponse } from 'next/server';

import { getBackendURL, isDemoMode } from '../../../../lib/api';

export async function POST() {
  if (isDemoMode()) {
    return NextResponse.json(createDemoImportJob('succeeded'), { status: 202 });
  }

  const response = await fetch(`${getBackendURL()}/v1/dashboard/import-jobs`, {
    method: 'POST',
    cache: 'no-store',
    headers: {
      Accept: 'application/json',
    },
  });

  const body = await response.text();
  return new NextResponse(body, {
    status: response.status,
    headers: {
      'Content-Type': response.headers.get('Content-Type') ?? 'application/json',
    },
  });
}

function createDemoImportJob(status: 'succeeded' | 'running') {
  const now = new Date().toISOString();
  return {
    id: 'demo-import-job',
    user_id: 'demo-user',
    status,
    stage: status === 'succeeded' ? 'completed' : 'gmail_fetching',
    imported_count: status === 'succeeded' ? 11 : 4,
    total_count: 11,
    source_records: 0,
    changed_entities: 0,
    refreshed_entities: 0,
    result_status: status === 'succeeded' ? 'ready' : null,
    error_message: null,
    created_at: now,
    started_at: now,
    stage_started_at: now,
    stage_durations: status === 'succeeded' ? { gmail_fetching: 1.2 } : {},
    completed_at: status === 'succeeded' ? now : null,
    updated_at: now,
  };
}
