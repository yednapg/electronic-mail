import { NextResponse } from 'next/server';

import { getMailboxSyncState } from '../../../../lib/api';
import { getRequestCookieHeader, requireConnectedGoogleAccount } from '../../../../lib/api-auth';

export async function GET(request: Request) {
  const cookie = getRequestCookieHeader(request);
  const unauthorized = await requireConnectedGoogleAccount(request);
  if (unauthorized) {
    return unauthorized;
  }

  try {
    return NextResponse.json(await getMailboxSyncState({ cookie }));
  } catch (_error) {
    return NextResponse.json({ detail: 'Unable to load mailbox sync state' }, { status: 503 });
  }
}
