import { NextResponse } from 'next/server';

import { getBackendURL } from '../../../../lib/api';
import { backendProxyHeaders, requireConnectedGoogleAccount } from '../../../../lib/api-auth';

export async function GET(request: Request) {
  const unauthorized = await requireConnectedGoogleAccount(request);
  if (unauthorized) {
    return unauthorized;
  }

  const response = await fetch(`${getBackendURL()}/v1/post-login/readiness`, {
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
