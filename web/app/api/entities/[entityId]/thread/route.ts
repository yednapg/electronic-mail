import { NextResponse } from 'next/server';

import { getBackendURL } from '../../../../../lib/api';
import { backendProxyHeaders, requireConnectedGoogleAccount } from '../../../../../lib/api-auth';

type ThreadRouteContext = {
  params: Promise<{
    entityId: string;
  }>;
};

export async function GET(request: Request, { params }: ThreadRouteContext) {
  const unauthorized = await requireConnectedGoogleAccount(request);
  if (unauthorized) {
    return unauthorized;
  }

  const { entityId } = await params;
  const requestUrl = new URL(request.url);

  const backendParams = new URLSearchParams();
  for (const key of ['limit', 'offset', 'threadId']) {
    const value = requestUrl.searchParams.get(key);
    if (value !== null) {
      backendParams.set(key, value);
    }
  }
  const query = backendParams.toString();
  const response = await fetch(`${getBackendURL()}/v1/entities/${encodeURIComponent(entityId)}/thread${query ? `?${query}` : ''}`, {
    cache: 'no-store',
    headers: backendProxyHeaders(request, {
      Accept: 'application/json',
    }),
  });

  const body = await response.text();
  return new NextResponse(body, {
    status: response.status,
    headers: {
      'Content-Type': response.headers.get('Content-Type') ?? 'application/json',
    },
  });
}
