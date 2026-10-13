import { createHash } from 'node:crypto';

import { NextResponse } from 'next/server';

import { getGoogleAuthState } from './api';

const AUTH_CACHE_MS = 5000;
// Bounded so unauthenticated callers rotating Cookie headers cannot grow
// process memory; each entry is a 64-char digest plus a boolean.
const AUTH_CACHE_MAX_ENTRIES = 512;

type CachedAuthState = { connected: boolean; expiresAt: number };

// Keys are SHA-256 digests of the Cookie header. The raw header carries the
// app session token and must never be held as a long-lived map key.
const cachedAuthStates = new Map<string, CachedAuthState>();
const pendingAuthStates = new Map<string, Promise<{ connected: boolean }>>();

export async function requireConnectedGoogleAccount(request?: Request): Promise<NextResponse | null> {
  const cookie = getRequestCookieHeader(request);
  if (cookie === null) {
    // Without a cookie the backend cannot resolve a session, so the answer is
    // known without a round-trip and must not occupy cache capacity.
    return disconnectedResponse();
  }
  const cacheKey = authCacheKey(cookie);
  const now = Date.now();
  const cachedAuthState = cachedAuthStates.get(cacheKey) ?? null;
  if (cachedAuthState && cachedAuthState.expiresAt > now) {
    return cachedAuthState.connected ? null : disconnectedResponse();
  }

  try {
    let pendingAuthState = pendingAuthStates.get(cacheKey) ?? null;
    pendingAuthState ??= getGoogleAuthState({ cookie })
      .then((auth) => ({ connected: auth.connected }))
      .finally(() => {
        pendingAuthStates.delete(cacheKey);
      });
    pendingAuthStates.set(cacheKey, pendingAuthState);
    const auth = await pendingAuthState;
    rememberAuthState(cacheKey, { connected: auth.connected, expiresAt: now + AUTH_CACHE_MS }, now);
    if (auth.connected) {
      return null;
    }
  } catch {
    return NextResponse.json({ detail: 'Unable to verify Google connection' }, { status: 503 });
  }

  return disconnectedResponse();
}

export function authCacheKey(cookie: string): string {
  return createHash('sha256').update(cookie, 'utf8').digest('hex');
}

export function authCacheSize(): number {
  return cachedAuthStates.size;
}

export function clearAuthCache(): void {
  cachedAuthStates.clear();
  pendingAuthStates.clear();
}

function rememberAuthState(cacheKey: string, state: CachedAuthState, now: number): void {
  // Map preserves insertion order, so deleting and re-inserting keeps the
  // most recently verified identities at the tail and evicts the oldest first.
  cachedAuthStates.delete(cacheKey);
  for (const [key, entry] of cachedAuthStates) {
    if (entry.expiresAt <= now) {
      cachedAuthStates.delete(key);
    }
  }
  while (cachedAuthStates.size >= AUTH_CACHE_MAX_ENTRIES) {
    const oldestKey = cachedAuthStates.keys().next().value;
    if (oldestKey === undefined) {
      break;
    }
    cachedAuthStates.delete(oldestKey);
  }
  cachedAuthStates.set(cacheKey, state);
}

function disconnectedResponse(): NextResponse {
  return NextResponse.json({ detail: 'Google account is not connected' }, { status: 401 });
}

export function getRequestCookieHeader(request: Request | undefined): string | null {
  const cookie = request?.headers.get('cookie') ?? null;
  return cookie !== null && cookie.length > 0 ? cookie : null;
}

export function backendProxyHeaders(
  request: Request | undefined,
  headers: Record<string, string> = {},
): HeadersInit {
  const cookie = getRequestCookieHeader(request);
  return cookie === null ? headers : { ...headers, Cookie: cookie };
}
