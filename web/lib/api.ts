/** Dashboard fetcher shared by the server-rendered dashboard page. */
import type { DashboardResponse } from './types';

export async function getDashboard(): Promise<DashboardResponse> {
  const res = await fetch('http://localhost:3001/dashboard', {
    cache: 'no-store',
  });
  if (!res.ok) throw new Error('Failed to fetch dashboard');
  return res.json();
}
