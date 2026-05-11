import { NextResponse } from 'next/server';

import { getBackendURL, isDemoMode } from '../../../../../lib/api';

type CompleteRouteContext = {
  params: Promise<{
    entityId: string;
  }>;
};

export async function POST(_request: Request, { params }: CompleteRouteContext) {
  const { entityId } = await params;

  if (isDemoMode()) {
    const now = new Date().toISOString();
    return NextResponse.json({
      id: `demo-complete-${entityId}`,
      user_id: 'demo-user',
      entity_id: entityId,
      outcome_type: 'complete',
      snooze_until: null,
      note: null,
      created_at: now,
    });
  }

  const response = await fetch(`${getBackendURL()}/v1/entities/${encodeURIComponent(entityId)}/complete`, {
    method: 'POST',
    cache: 'no-store',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: '{}',
  });

  const body = await response.text();
  return new NextResponse(body, {
    status: response.status,
    headers: {
      'Content-Type': response.headers.get('Content-Type') ?? 'application/json',
    },
  });
}
