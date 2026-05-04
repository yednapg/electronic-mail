/** Dashboard fetcher shared by the server-rendered dashboard page. */
import type { DashboardResponse } from './types';
import { demoDashboard } from './demo-dashboard';

const DEFAULT_BACKEND_URL = 'http://localhost:3001';

export function getBackendURL(): string {
  return (process.env.DECISION_PIPELINE_BACKEND_URL ?? DEFAULT_BACKEND_URL).replace(/\/+$/, '');
}

export function isDemoMode(): boolean {
  return process.env.NEXT_PUBLIC_DEMO_MODE !== 'false';
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
