import { FeedResponse } from './types';

export async function getFeed(): Promise<FeedResponse> {
  const res = await fetch('http://localhost:3001/feed', {
    cache: 'no-store',
  });
  if (!res.ok) throw new Error('Failed to fetch feed');
  return res.json();
}
