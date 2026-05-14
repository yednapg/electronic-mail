import { NextResponse } from 'next/server';

import { getBackendURL } from '../../../../lib/api';
import { backendProxyHeaders, requireConnectedGoogleAccount } from '../../../../lib/api-auth';

export async function POST(request: Request) {
  const unauthorized = await requireConnectedGoogleAccount(request);
  if (unauthorized) {
    return unauthorized;
  }

  const response = await fetch(`${getBackendURL()}/v1/dashboard/prepare`, {
    method: 'POST',
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
