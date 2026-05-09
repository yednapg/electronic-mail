import { NextResponse } from 'next/server';

import { getBackendURL, isDemoMode } from '../../../../../lib/api';
import { getDemoThread } from '../../../../../lib/demo-evidence';

type ThreadRouteContext = {
  params: Promise<{
    entityId: string;
  }>;
};

export async function GET(request: Request, { params }: ThreadRouteContext) {
  const { entityId } = await params;
  const requestUrl = new URL(request.url);

  if (isDemoMode()) {
    const thread = getDemoThread(entityId, {
      limit: parseIntegerSearchParam(requestUrl.searchParams.get('limit')),
      offset: parseIntegerSearchParam(requestUrl.searchParams.get('offset')),
    });
    if (thread === null) {
      return NextResponse.json({ detail: 'Entity not found' }, { status: 404 });
    }
    return NextResponse.json(thread);
  }

  const backendParams = new URLSearchParams();
  for (const key of ['limit', 'offset']) {
    const value = requestUrl.searchParams.get(key);
    if (value !== null) {
      backendParams.set(key, value);
    }
  }
  const query = backendParams.toString();
  const response = await fetch(`${getBackendURL()}/v1/entities/${encodeURIComponent(entityId)}/thread${query ? `?${query}` : ''}`, {
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

function parseIntegerSearchParam(value: string | null): number | undefined {
  if (value === null) {
    return undefined;
  }

  const parsed = Number(value);
  return Number.isInteger(parsed) ? parsed : undefined;
}
