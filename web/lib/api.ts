/** Feed fetcher shared by the server-rendered dashboard page. */
import type { FeedResponse } from './types';

export async function getFeed(): Promise<FeedResponse> {
  // Access env defensively so the same module works in tests without assuming Node globals.
  const env = (
    globalThis as { process?: { env?: Record<string, string | undefined> } }
  ).process?.env;

  if (env?.NEXT_PUBLIC_USE_DEV_DATA === 'true') {
    // These fixtures let the UI render stable scenarios without a running backend.
    const mode = env.NEXT_PUBLIC_DEV_SCENARIO;

    if (mode === 'afternoon') {
      return (await import('@/dev-data/feed-afternoon.json')).default as FeedResponse;
    }

    if (mode === 'night') {
      return (await import('@/dev-data/feed-night.json')).default as FeedResponse;
    }

    if (mode === 'chaos') {
      return (await import('@/dev-data/feed-chaos.json')).default as FeedResponse;
    }

    return (await import('@/dev-data/feed-morning.json')).default as FeedResponse;
  }

  const res = await fetch('http://localhost:3001/feed', {
    cache: 'no-store',
  });
  if (!res.ok) throw new Error('Failed to fetch feed');
  return res.json();
}
