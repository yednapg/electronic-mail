import { NextResponse } from 'next/server';

import { getBackendURL, isDemoMode } from '../../../../lib/api';

export async function POST() {
  if (isDemoMode()) {
    return NextResponse.json({ status: 'ready', demo: true });
  }

  const response = await fetch(`${getBackendURL()}/v1/dashboard/prepare`, {
    method: 'POST',
    cache: 'no-store',
    headers: {
      Accept: 'application/json',
    },
  });

  const body = await response.text();
  return new NextResponse(body, {
    status: response.status,
    headers: {
      'Content-Type': response.headers.get('Content-Type') ?? 'application/json',
    },
  });
}
