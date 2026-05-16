import { NextResponse } from 'next/server';

import { getMailbox } from '../../../lib/api';
import { getRequestCookieHeader, requireConnectedGoogleAccount } from '../../../lib/api-auth';
import type { MailboxLabel } from '../../../lib/types';

const MAILBOX_LABELS = new Set(['inbox', 'sent', 'drafts', 'trash', 'archive', 'all']);

export async function GET(request: Request) {
  const searchParams = new URL(request.url).searchParams;
  const rawLabel = searchParams.get('label') ?? 'inbox';
  const label = MAILBOX_LABELS.has(rawLabel) ? (rawLabel as MailboxLabel) : 'inbox';
  const limit = parseBoundedInteger(searchParams.get('limit'), 100, 1, 1000);
  const cursor = searchParams.get('cursor');
  const cookie = getRequestCookieHeader(request);
  const authPromise = requireConnectedGoogleAccount(request);
  const mailboxPromise = getMailbox({ label, limit, cursor }, { cookie });
  const unauthorized = await authPromise;
  if (unauthorized) {
    void mailboxPromise.catch(() => {});
    return unauthorized;
  }

  try {
    return NextResponse.json(await mailboxPromise);
  } catch (_error) {
    return NextResponse.json({ detail: 'Unable to load mailbox' }, { status: 503 });
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
