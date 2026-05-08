import { NextResponse } from 'next/server';

import { getBackendURL, isDemoMode } from '../../../../../lib/api';
import { getDemoThread } from '../../../../../lib/demo-evidence';

type ThreadRouteContext = {
  params: Promise<{
    entityId: string;
  }>;
};

export async function GET(_request: Request, { params }: ThreadRouteContext) {
  const { entityId } = await params;

  if (isDemoMode()) {
    const thread = getDemoThread(entityId);
    if (thread === null) {
      return NextResponse.json({ detail: 'Entity not found' }, { status: 404 });
    }
    return NextResponse.json(thread);
  }

  const response = await fetch(`${getBackendURL()}/v1/entities/${encodeURIComponent(entityId)}/thread`, {
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
