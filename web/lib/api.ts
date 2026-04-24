/** Dashboard fetcher shared by the server-rendered dashboard page. */
import type { DashboardResponse } from './types';

const DEFAULT_BACKEND_URL = 'http://localhost:3001';

export function getBackendURL(): string {
  return (process.env.DECISION_PIPELINE_BACKEND_URL ?? DEFAULT_BACKEND_URL).replace(/\/+$/, '');
}

export async function getDashboard(): Promise<DashboardResponse> {
  const res = await fetch(`${getBackendURL()}/dashboard`, {
    cache: 'no-store',
  });
  if (!res.ok) throw new Error('Failed to fetch dashboard');
  return res.json();
}
