/** Dashboard fetcher shared by server-rendered app routes. */
import type { DashboardResponse, HistoryResponse, ThreadReaderResponse } from './types';
import type {
  EntityOutcomeResponse,
  GmailDraftRequest,
  GmailDraftResponse,
  TaskCreateRequest,
  TaskResponse,
} from '@electronic-mail/types';
import { demoDashboard } from './demo-dashboard';
import { getDemoThread } from './demo-evidence';
import { demoHistory } from './demo-history';

const DEFAULT_BACKEND_URL = 'http://localhost:3001';

export type EntityThreadOptions = {
  limit?: number;
  offset?: number;
};

export function getBackendURL(): string {
  return (process.env.ELECTRONIC_MAIL_BACKEND_URL ?? DEFAULT_BACKEND_URL).replace(/\/+$/, '');
}

export function isDemoMode(): boolean {
  return process.env.NEXT_PUBLIC_DEMO_MODE === 'true';
}

export async function getDashboard(): Promise<DashboardResponse> {
  if (isDemoMode()) {
    return demoDashboard;
  }

  const res = await fetch(`${getBackendURL()}/dashboard`, {
    cache: 'no-store',
  });
  if (!res.ok) throw new Error('Failed to fetch dashboard');
  return res.json();
}

export async function getHistory({ limit = 60, offset = 0 } = {}): Promise<HistoryResponse> {
  if (isDemoMode()) {
    return demoHistory;
  }

  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  const res = await fetch(`${getBackendURL()}/v1/history?${params.toString()}`, {
    cache: 'no-store',
  });
  if (!res.ok) throw new Error('Failed to fetch history');
  return res.json();
}

export async function getEntityThread(
  entityId: string,
  { limit, offset }: EntityThreadOptions = {},
): Promise<ThreadReaderResponse> {
  if (isDemoMode()) {
    const thread = getDemoThread(entityId, { limit, offset });
    if (thread === null) {
      throw new Error('Thread not found');
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
  const url = `${getBackendURL()}/v1/entities/${encodeURIComponent(entityId)}/thread${query ? `?${query}` : ''}`;

  const res = await fetch(url, {
    cache: 'no-store',
  });
  if (!res.ok) throw new Error('Failed to fetch entity thread');
  return res.json();
}

export async function createTask(request: TaskCreateRequest): Promise<TaskResponse> {
  return postJSON('/v1/tasks', request);
}

export async function createGmailDraft(request: GmailDraftRequest): Promise<GmailDraftResponse> {
  return postJSON('/v1/gmail/drafts', request);
}

export async function completeEntity(entityId: string): Promise<EntityOutcomeResponse> {
  return postJSON(`/v1/entities/${encodeURIComponent(entityId)}/complete`, {});
}

export async function snoozeEntity(entityId: string, snoozeUntil: string): Promise<EntityOutcomeResponse> {
  return postJSON(`/v1/entities/${encodeURIComponent(entityId)}/snooze`, { snooze_until: snoozeUntil });
}

export async function dismissEntity(entityId: string): Promise<EntityOutcomeResponse> {
  return postJSON(`/v1/entities/${encodeURIComponent(entityId)}/dismiss`, {});
}

async function postJSON<Response>(path: string, body: unknown): Promise<Response> {
  const res = await fetch(`${getBackendURL()}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Backend request failed: ${path}`);
  return res.json();
}
