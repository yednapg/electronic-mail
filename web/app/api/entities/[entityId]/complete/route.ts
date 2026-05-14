import { NextResponse } from 'next/server';

import { getBackendURL } from '../../../../../lib/api';
import { backendProxyHeaders, requireConnectedGoogleAccount } from '../../../../../lib/api-auth';

type CompleteRouteContext = {
  params: Promise<{
    entityId: string;
  }>;
};

export async function POST(request: Request, { params }: CompleteRouteContext) {
  const unauthorized = await requireConnectedGoogleAccount(request);
  if (unauthorized) {
    return unauthorized;
  }

  const { entityId } = await params;

  const response = await fetch(`${getBackendURL()}/v1/entities/${encodeURIComponent(entityId)}/complete`, {
    method: 'POST',
    cache: 'no-store',
    headers: backendProxyHeaders(request, {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    }),
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
