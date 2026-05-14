import { NextResponse } from 'next/server';

import { getBackendURL } from '../../../lib/api';
import { backendProxyHeaders, requireConnectedGoogleAccount } from '../../../lib/api-auth';

export async function GET(request: Request) {
  const authResponse = await requireConnectedGoogleAccount(request);
  if (authResponse !== null) {
    return authResponse;
  }

  const response = await fetch(`${getBackendURL()}/dashboard`, {
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
