import { NextResponse } from 'next/server';

import { getBackendURL } from '../../../../../lib/api';
import { backendProxyHeaders, requireConnectedGoogleAccount } from '../../../../../lib/api-auth';

type RouteContext = {
  params: Promise<{ jobId: string }> | { jobId: string };
};

export async function GET(request: Request, context: RouteContext) {
  const unauthorized = await requireConnectedGoogleAccount(request);
  if (unauthorized) {
    return unauthorized;
  }

  const { jobId } = await context.params;

  const response = await fetch(`${getBackendURL()}/v1/dashboard/import-jobs/${encodeURIComponent(jobId)}`, {
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
