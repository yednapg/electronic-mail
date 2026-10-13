import assert from 'node:assert/strict';
import test from 'node:test';

import {
  authCacheKey,
  authCacheSize,
  clearAuthCache,
  requireConnectedGoogleAccount,
} from './api-auth';

type FetchStub = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

function withFetch<T>(stub: FetchStub, run: () => Promise<T>): Promise<T> {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = stub as typeof fetch;
  return run().finally(() => {
    globalThis.fetch = originalFetch;
    clearAuthCache();
  });
}

function requestWithCookie(cookie: string | null): Request {
  return new Request('http://web.local/api/mailbox', {
    headers: cookie === null ? {} : { cookie },
  });
}

test('auth cache keys are digests, never the raw cookie header', () => {
  const cookie = 'dp_session=super-secret-token; theme=dark';
  const key = authCacheKey(cookie);
  assert.match(key, /^[0-9a-f]{64}$/);
  assert.ok(!key.includes('super-secret-token'));
  assert.equal(authCacheKey(cookie), key);
  assert.notEqual(authCacheKey('dp_session=other'), key);
});

test('requests without a cookie are rejected locally and never cached', async () => {
  let backendCalls = 0;
  await withFetch(async () => {
    backendCalls += 1;
    return new Response(JSON.stringify({ connected: true }), { status: 200 });
  }, async () => {
    const response = await requireConnectedGoogleAccount(requestWithCookie(null));
    assert.equal(response?.status, 401);
    assert.equal(backendCalls, 0);
    assert.equal(authCacheSize(), 0);
  });
});

test('connected sessions are cached per digest and reuse the backend answer', async () => {
  let backendCalls = 0;
  await withFetch(async () => {
    backendCalls += 1;
    return new Response(JSON.stringify({ connected: true }), { status: 200 });
  }, async () => {
    const request = requestWithCookie('dp_session=alpha');
    assert.equal(await requireConnectedGoogleAccount(request), null);
    assert.equal(await requireConnectedGoogleAccount(request), null);
    assert.equal(backendCalls, 1);
    assert.equal(authCacheSize(), 1);
  });
});

test('the auth cache stays bounded when cookies rotate', async () => {
  await withFetch(async () => (
    new Response(JSON.stringify({ connected: false }), { status: 200 })
  ), async () => {
    for (let index = 0; index < 700; index += 1) {
      const response = await requireConnectedGoogleAccount(requestWithCookie(`dp_session=rotating-${index}`));
      assert.equal(response?.status, 401);
    }
    assert.ok(authCacheSize() <= 512, `cache grew to ${authCacheSize()} entries`);
  });
});

test('backend failures surface as 503 without poisoning the cache', async () => {
  await withFetch(async () => {
    throw new Error('backend down');
  }, async () => {
    const response = await requireConnectedGoogleAccount(requestWithCookie('dp_session=beta'));
    assert.equal(response?.status, 503);
    assert.equal(authCacheSize(), 0);
  });
});
