import { NextResponse } from 'next/server';

import { getGoogleAuthState } from './api';

const AUTH_CACHE_MS = 5000;

const cachedAuthStates = new Map<string, { connected: boolean; expiresAt: number }>();
const pendingAuthStates = new Map<string, Promise<{ connected: boolean }>>();

export async function requireConnectedGoogleAccount(request?: Request): Promise<NextResponse | null> {
  const cookie = getRequestCookieHeader(request);
  const cacheKey = cookie ?? 'no-cookie';
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
    cachedAuthStates.set(cacheKey, {
      connected: auth.connected,
      expiresAt: now + AUTH_CACHE_MS,
    });
    if (auth.connected) {
      return null;
    }
  } catch {
    return NextResponse.json({ detail: 'Unable to verify Google connection' }, { status: 503 });
  }

  return disconnectedResponse();
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
