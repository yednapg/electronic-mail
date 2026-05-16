/** Dashboard fetcher shared by server-rendered app routes. */
import type {
  DashboardResponse,
  AppSessionStateResponse,
  GmailViewResponse,
  GoogleAuthState,
  MailboxLabel,
  MailboxResponse,
  MailboxSyncStateResponse,
  MailboxSyncTriggerResponse,
  ThreadReaderResponse,
} from './types';
import type { FirstRunImportJobResponse } from '@electronic-mail/types';
import { getDemoAppSession, getDemoDashboard, getDemoMailbox, getDemoThreadReader } from './demo-data';
import { isDemoMode } from './demo-mode';

const DEFAULT_BACKEND_URL = 'http://localhost:3001';

export type MailGroupDetailOptions = {
  limit?: number;
  offset?: number;
};

export type BackendRequestOptions = {
  cookie?: string | null;
  headers?: HeadersInit;
};

export function getBackendURL(): string {
  return (process.env.ELECTRONIC_MAIL_BACKEND_URL ?? DEFAULT_BACKEND_URL).replace(/\/+$/, '');
}

export async function getAppSession(options: BackendRequestOptions = {}): Promise<AppSessionStateResponse> {
  if (isDemoMode()) {
    return getDemoAppSession();
  }
  const res = await fetch(`${getBackendURL()}/v1/app/session`, {
    cache: 'no-store',
    headers: backendHeaders(options),
  });
  if (!res.ok) throw new Error('Failed to fetch app session');
  return res.json();
}

export async function getDashboard(options: BackendRequestOptions = {}): Promise<DashboardResponse> {
  if (isDemoMode()) {
    return getDemoDashboard();
  }
  const res = await fetch(`${getBackendURL()}/dashboard`, {
    cache: 'no-store',
    headers: backendHeaders(options),
  });
  if (!res.ok) throw new Error('Failed to fetch dashboard');
  return res.json();
}

export async function getGoogleAuthState(options: BackendRequestOptions = {}): Promise<GoogleAuthState> {
  const res = await fetch(`${getBackendURL()}/v1/auth/google/state`, {
    cache: 'no-store',
    headers: backendHeaders(options),
  });
  if (!res.ok) throw new Error('Failed to fetch Google auth state');
  return res.json();
}

export async function getLatestFirstRunImportJob(
  options: BackendRequestOptions = {},
): Promise<FirstRunImportJobResponse | null> {
  if (isDemoMode()) {
    return {
      id: 'demo-import-job',
      user_id: 'demo-user',
      status: 'succeeded',
      stage: 'ready',
      fetched_count: 6,
      total_count: 6,
      thread_count: 6,
      dashboard_item_count: 5,
      inbox_ready_at: '2026-05-16T09:30:00+05:30',
      first_groups_ready_at: '2026-05-16T09:30:00+05:30',
      dashboard_ready_at: '2026-05-16T09:30:00+05:30',
      canonical_dashboard_ready_at: '2026-05-16T09:30:00+05:30',
      quality_status: 'ready',
      quality_error: null,
      full_import_started_at: null,
      full_import_completed_at: '2026-05-16T09:30:00+05:30',
      error_message: null,
      created_at: '2026-05-16T09:30:00+05:30',
      started_at: '2026-05-16T09:30:00+05:30',
      completed_at: '2026-05-16T09:30:00+05:30',
      updated_at: '2026-05-16T09:30:00+05:30',
    };
  }
  const res = await fetch(`${getBackendURL()}/v1/first-run/import-jobs/latest`, {
    cache: 'no-store',
    headers: backendHeaders(options),
  });
  if (res.status === 404) {
    return null;
  }
  if (!res.ok) throw new Error('Failed to fetch latest first-run import job');
  return res.json();
}

export async function getGmailView(options: BackendRequestOptions = {}): Promise<GmailViewResponse> {
  const res = await fetch(`${getBackendURL()}/v1/gmail-view`, {
    cache: 'no-store',
    headers: backendHeaders(options),
  });
  if (!res.ok) throw new Error('Failed to fetch Gmail view');
  return res.json();
}

export async function getMailbox({
  label = 'inbox',
  limit,
  cursor,
}: {
  label?: MailboxLabel;
  limit?: number;
  cursor?: string | null;
} = {},
options: BackendRequestOptions = {}): Promise<MailboxResponse> {
  if (isDemoMode()) {
    return getDemoMailbox();
  }
  const params = new URLSearchParams({ label });
  if (limit !== undefined) {
    params.set('limit', String(limit));
  }
  if (cursor) {
    params.set('cursor', cursor);
  }
  const res = await fetch(`${getBackendURL()}/v1/mailbox?${params.toString()}`, {
    cache: 'no-store',
    headers: backendHeaders(options),
  });
  if (!res.ok) throw new Error('Failed to fetch mailbox');
  return res.json();
}

export async function getMailboxThread(
  threadId: string,
  { limit, offset }: MailGroupDetailOptions = {},
  options: BackendRequestOptions = {},
): Promise<ThreadReaderResponse> {
  if (isDemoMode()) {
    const thread = getDemoThreadReader(threadId);
    if (thread === null) {
      throw new Error('Failed to fetch mailbox thread');
    }
    return thread;
  }
  const params = new URLSearchParams();
  if (limit !== undefined) {
    params.set('limit', String(limit));
  }
  if (offset !== undefined) {
    params.set('offset', String(offset));
  }
  const query = params.toString();
  const res = await fetch(`${getBackendURL()}/v1/mailbox/threads/${encodeURIComponent(threadId)}${query ? `?${query}` : ''}`, {
    cache: 'no-store',
    headers: backendHeaders(options),
  });
  if (!res.ok) throw new Error('Failed to fetch mailbox thread');
  return res.json();
}

export async function getMailboxSyncState(options: BackendRequestOptions = {}): Promise<MailboxSyncStateResponse> {
  const res = await fetch(`${getBackendURL()}/v1/mailbox/sync-state`, {
    cache: 'no-store',
    headers: backendHeaders(options),
  });
  if (!res.ok) throw new Error('Failed to fetch mailbox sync state');
  return res.json();
}

export async function triggerMailboxSync(options: BackendRequestOptions = {}): Promise<MailboxSyncTriggerResponse> {
  return postJSON('/v1/mailbox/sync', {}, options);
}

async function postJSON<Response>(
  path: string,
  body: unknown,
  options: BackendRequestOptions = {},
): Promise<Response> {
  const res = await fetch(`${getBackendURL()}${path}`, {
    method: 'POST',
    headers: backendHeaders({
      ...options,
      headers: {
        ...headersToObject(options.headers),
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
    }),
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Backend request failed: ${path}`);
  return res.json();
}

function backendHeaders(options: BackendRequestOptions = {}): HeadersInit {
  const headers = headersToObject(options.headers);
  if (options.cookie !== undefined && options.cookie !== null && options.cookie.length > 0) {
    headers.Cookie = options.cookie;
  }
  return headers;
}

function headersToObject(headers: HeadersInit | undefined): Record<string, string> {
  if (headers === undefined) {
    return {};
  }
  if (headers instanceof Headers) {
    return Object.fromEntries(headers.entries());
  }
  if (Array.isArray(headers)) {
    return Object.fromEntries(headers);
  }
  return { ...headers };
}
