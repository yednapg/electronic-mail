import { NextResponse } from 'next/server';

import { getBackendURL, isDemoMode } from '../../../../../../lib/api';

const ALLOWED_OPERATIONS = new Set(['archive', 'unarchive', 'mark-read']);

type RouteContext = {
  params: Promise<{
    threadId: string;
    operation: string;
  }>;
};

export async function POST(_request: Request, { params }: RouteContext) {
  const { threadId, operation } = await params;

  if (!ALLOWED_OPERATIONS.has(operation)) {
    return NextResponse.json({ detail: 'Unsupported Gmail thread operation' }, { status: 400 });
  }

  if (isDemoMode()) {
    return NextResponse.json({ thread_id: threadId, action: operation });
  }

  const response = await fetch(
    `${getBackendURL()}/v1/gmail/threads/${encodeURIComponent(threadId)}/${operation}`,
    {
      method: 'POST',
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
      },
    },
  );

  const body = await response.text();
  return new NextResponse(body, {
    status: response.status,
    headers: {
      'Content-Type': response.headers.get('Content-Type') ?? 'application/json',
    },
  });
}
