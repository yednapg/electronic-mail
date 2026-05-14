import { NextResponse } from 'next/server';

import { getBackendURL } from '../../../../../../lib/api';
import { backendProxyHeaders, requireConnectedGoogleAccount } from '../../../../../../lib/api-auth';

const ALLOWED_OPERATIONS = new Set(['archive', 'unarchive', 'mark-read']);

type RouteContext = {
  params: Promise<{
    threadId: string;
    operation: string;
  }>;
};

export async function POST(request: Request, { params }: RouteContext) {
  const unauthorized = await requireConnectedGoogleAccount(request);
  if (unauthorized) {
    return unauthorized;
  }

  const { threadId, operation } = await params;

  if (!ALLOWED_OPERATIONS.has(operation)) {
    return NextResponse.json({ detail: 'Unsupported Gmail thread operation' }, { status: 400 });
  }

  const response = await fetch(
    `${getBackendURL()}/v1/gmail/threads/${encodeURIComponent(threadId)}/${operation}`,
    {
      method: 'POST',
      cache: 'no-store',
      headers: backendProxyHeaders(request, {
        Accept: 'application/json',
      }),
    },
  );
  if (response.ok) {
    await fetch(`${getBackendURL()}/v1/mailbox/sync`, {
      method: 'POST',
      cache: 'no-store',
      headers: backendProxyHeaders(request, {
        Accept: 'application/json',
      }),
    }).catch(() => null);
  }

  const body = await response.text();
  return new NextResponse(body, {
    status: response.status,
    headers: {
      'Content-Type': response.headers.get('Content-Type') ?? 'application/json',
    },
  });
}
