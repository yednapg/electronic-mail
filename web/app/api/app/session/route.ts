import { NextResponse } from 'next/server';

import { getBackendURL } from '../../../../lib/api';
import { backendProxyHeaders, requireConnectedGoogleAccount } from '../../../../lib/api-auth';

export async function GET(request: Request) {
  const unauthorized = await requireConnectedGoogleAccount(request);
  if (unauthorized) {
    return unauthorized;
  }

  const response = await fetch(`${getBackendURL()}/v1/app/session`, {
    cache: 'no-store',
    headers: backendProxyHeaders(request, {
      Accept: 'application/json',
    }),
  });

  return new NextResponse(response.body, {
    status: response.status,
    headers: {
      'Content-Type': response.headers.get('Content-Type') ?? 'application/json',
    },
  });
}
