import { NextResponse } from 'next/server';

import { getBackendURL, isDemoMode } from '../../../../../lib/api';

type RouteContext = {
  params: Promise<{ jobId: string }> | { jobId: string };
};

export async function GET(_request: Request, context: RouteContext) {
  const { jobId } = await context.params;

  if (isDemoMode()) {
    return NextResponse.json(createDemoImportJob(jobId));
  }

  const response = await fetch(`${getBackendURL()}/v1/dashboard/import-jobs/${encodeURIComponent(jobId)}`, {
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

function createDemoImportJob(jobId: string) {
  const now = new Date().toISOString();
  return {
    id: jobId,
    user_id: 'demo-user',
    status: 'succeeded',
    stage: 'completed',
    imported_count: 11,
    total_count: 11,
    source_records: 0,
    changed_entities: 0,
    refreshed_entities: 0,
    result_status: 'ready',
    error_message: null,
    created_at: now,
    started_at: now,
    stage_started_at: now,
    stage_durations: { gmail_fetching: 1.2 },
    completed_at: now,
    updated_at: now,
  };
}
