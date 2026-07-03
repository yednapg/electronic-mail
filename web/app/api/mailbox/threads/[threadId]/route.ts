import { NextResponse } from 'next/server';

import { getMailboxThread } from '../../../../../lib/api';
import { getRequestCookieHeader, requireConnectedGoogleAccount } from '../../../../../lib/api-auth';

type MailboxThreadRouteContext = {
  params: Promise<{
    threadId: string;
  }>;
};

export async function GET(request: Request, { params }: MailboxThreadRouteContext) {
  const { threadId } = await params;
  const searchParams = new URL(request.url).searchParams;
  const limit = parseBoundedInteger(searchParams.get('limit'), 25, 1, 100);
  const offset = parseBoundedInteger(searchParams.get('offset'), 0, 0, Number.MAX_SAFE_INTEGER);
  const includeSummary = searchParams.get('include_summary') === 'true';
  const cookie = getRequestCookieHeader(request);
  const authPromise = requireConnectedGoogleAccount(request);
  const threadPromise = getMailboxThread(threadId, { limit, offset, includeSummary }, { cookie });
  const unauthorized = await authPromise;
  if (unauthorized) {
    void threadPromise.catch(() => {});
    return unauthorized;
  }

  try {
    return NextResponse.json(await threadPromise);
  } catch (_error) {
    return NextResponse.json({ detail: 'Thread not found' }, { status: 404 });
  }
}

function parseBoundedInteger(value: string | null, fallback: number, min: number, max: number): number {
  if (value === null) {
    return fallback;
  }
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < min) {
    return fallback;
  }
  return Math.min(parsed, max);
}
