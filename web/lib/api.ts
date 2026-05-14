/** Dashboard fetcher shared by server-rendered app routes. */
import type {
  DashboardResponse,
  GmailViewResponse,
  GoogleAuthState,
  MailboxLabel,
  MailboxResponse,
  MailboxSyncStateResponse,
  MailboxSyncTriggerResponse,
  ThreadReaderResponse,
} from './types';
import type {
  EntityOutcomeResponse,
  DashboardImportJobResponse,
  FirstRunImportJobResponse,
  GmailDraftRequest,
  GmailDraftResponse,
  TaskCreateRequest,
  TaskResponse,
} from '@decision-pipeline/types';

const DEFAULT_BACKEND_URL = 'http://localhost:3001';

export type EntityThreadOptions = {
  limit?: number;
  offset?: number;
  threadId?: string;
};

export type BackendRequestOptions = {
  cookie?: string | null;
  headers?: HeadersInit;
};

export function getBackendURL(): string {
  return (process.env.DECISION_PIPELINE_BACKEND_URL ?? DEFAULT_BACKEND_URL).replace(/\/+$/, '');
}

export async function getDashboard(options: BackendRequestOptions = {}): Promise<DashboardResponse> {
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

export async function getLatestDashboardImportJob(
  options: BackendRequestOptions = {},
): Promise<DashboardImportJobResponse | null> {
  const res = await fetch(`${getBackendURL()}/v1/dashboard/import-jobs/latest`, {
    cache: 'no-store',
    headers: backendHeaders(options),
  });
  if (res.status === 404) {
    return null;
  }
  if (!res.ok) throw new Error('Failed to fetch latest dashboard import job');
  return res.json();
}

export async function getLatestFirstRunImportJob(
  options: BackendRequestOptions = {},
): Promise<FirstRunImportJobResponse | null> {
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
  { limit, offset }: Omit<EntityThreadOptions, 'threadId'> = {},
  options: BackendRequestOptions = {},
): Promise<ThreadReaderResponse> {
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

export async function getEntityThread(
  entityId: string,
  { limit, offset, threadId }: EntityThreadOptions = {},
  options: BackendRequestOptions = {},
): Promise<ThreadReaderResponse> {
  const params = new URLSearchParams();
  if (limit !== undefined) {
    params.set('limit', String(limit));
  }
  if (offset !== undefined) {
    params.set('offset', String(offset));
  }
  if (threadId !== undefined && threadId.length > 0) {
    params.set('threadId', threadId);
  }
  const query = params.toString();
  const url = `${getBackendURL()}/v1/entities/${encodeURIComponent(entityId)}/thread${query ? `?${query}` : ''}`;

  const res = await fetch(url, {
    cache: 'no-store',
    headers: backendHeaders(options),
  });
  if (!res.ok) throw new Error('Failed to fetch entity thread');
  return res.json();
}

export async function createTask(request: TaskCreateRequest, options: BackendRequestOptions = {}): Promise<TaskResponse> {
  return postJSON('/v1/tasks', request, options);
}

export async function createGmailDraft(
  request: GmailDraftRequest,
  options: BackendRequestOptions = {},
): Promise<GmailDraftResponse> {
  return postJSON('/v1/gmail/drafts', request, options);
}

export async function completeEntity(
  entityId: string,
  options: BackendRequestOptions = {},
): Promise<EntityOutcomeResponse> {
  return postJSON(`/v1/entities/${encodeURIComponent(entityId)}/complete`, {}, options);
}

export async function snoozeEntity(
  entityId: string,
  snoozeUntil: string,
  options: BackendRequestOptions = {},
): Promise<EntityOutcomeResponse> {
  return postJSON(`/v1/entities/${encodeURIComponent(entityId)}/snooze`, { snooze_until: snoozeUntil }, options);
}

export async function dismissEntity(entityId: string, options: BackendRequestOptions = {}): Promise<EntityOutcomeResponse> {
  return postJSON(`/v1/entities/${encodeURIComponent(entityId)}/dismiss`, {}, options);
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
