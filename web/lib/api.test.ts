import test from 'node:test';
import assert from 'node:assert/strict';

import {
  getDashboard,
  getEntityThread,
  getGmailView,
  getGoogleAuthState,
  getMailbox,
  getMailboxSyncState,
  getMailboxThread,
  triggerMailboxSync,
} from './api';

test('gmail view fetcher always calls backend', async () => {
  const previousFetch = globalThis.fetch;
  const calls: string[] = [];

  globalThis.fetch = ((input: RequestInfo | URL) => {
    calls.push(String(input));
    return Promise.resolve(
      new Response(JSON.stringify({ total_threads: 0, sections: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  }) as typeof fetch;

  try {
    const gmail = await getGmailView();

    assert.equal(gmail.total_threads, 0);
    assert.equal(calls[0], 'http://localhost:3001/v1/gmail-view');
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test('dashboard fetcher always calls backend', async () => {
  const previousFetch = globalThis.fetch;
  const calls: string[] = [];

  globalThis.fetch = ((input: RequestInfo | URL) => {
    calls.push(String(input));
    return Promise.resolve(
      new Response(
        JSON.stringify({
          auth: { available: true, connected: false, connect_url: 'http://localhost:3001/auth/google' },
          profile: null,
          briefing: null,
          feed: { now: [], today: [], worth_knowing: [] },
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );
  }) as typeof fetch;

  try {
    const dashboard = await getDashboard();

    assert.equal(dashboard.auth.connected, false);
    assert.equal(calls[0], 'http://localhost:3001/dashboard');
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test('Google auth state fetcher uses the lightweight backend endpoint', async () => {
  const previousFetch = globalThis.fetch;
  const calls: string[] = [];

  globalThis.fetch = ((input: RequestInfo | URL) => {
    calls.push(String(input));
    return Promise.resolve(
      new Response(JSON.stringify({ available: true, connected: true, connect_url: null }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  }) as typeof fetch;

  try {
    const auth = await getGoogleAuthState();

    assert.equal(auth.connected, true);
    assert.equal(calls[0], 'http://localhost:3001/v1/auth/google/state');
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test('entity thread fetcher always calls backend with pagination', async () => {
  const previousFetch = globalThis.fetch;
  const calls: string[] = [];

  globalThis.fetch = ((input: RequestInfo | URL) => {
    calls.push(String(input));
    return Promise.resolve(
      new Response(
        JSON.stringify({
          entity_id: 'entity-123',
          total_records: 0,
          records: [],
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );
  }) as typeof fetch;

  try {
    const thread = await getEntityThread('entity-123', { limit: 10, offset: 20, threadId: 'gmail-thread-1' });

    assert.equal(thread.entity_id, 'entity-123');
    assert.equal(
      calls[0],
      'http://localhost:3001/v1/entities/entity-123/thread?limit=10&offset=20&threadId=gmail-thread-1',
    );
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test('mailbox fetcher calls backend with label pagination', async () => {
  const previousFetch = globalThis.fetch;
  const calls: string[] = [];

  globalThis.fetch = ((input: RequestInfo | URL) => {
    calls.push(String(input));
    return Promise.resolve(
      new Response(JSON.stringify({ label: 'inbox', total_threads: 0, next_cursor: null, sections: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  }) as typeof fetch;

  try {
    const mailbox = await getMailbox({ label: 'trash', limit: 50, cursor: '100' });

    assert.equal(mailbox.total_threads, 0);
    assert.equal(calls[0], 'http://localhost:3001/v1/mailbox?label=trash&limit=50&cursor=100');
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test('mailbox thread fetcher opens by Gmail thread id without entity id', async () => {
  const previousFetch = globalThis.fetch;
  const calls: string[] = [];

  globalThis.fetch = ((input: RequestInfo | URL) => {
    calls.push(String(input));
    return Promise.resolve(
      new Response(
        JSON.stringify({
          entity_id: 'gmail-thread:thread-1',
          user_id: 'local-user',
          source: 'gmail',
          gmail_thread_id: 'thread-1',
          subject: 'Thread subject',
          total_messages: 0,
          limit: 25,
          offset: 0,
          has_more: false,
          messages: [],
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );
  }) as typeof fetch;

  try {
    const thread = await getMailboxThread('thread-1', { limit: 25, offset: 0 });

    assert.equal(thread.gmail_thread_id, 'thread-1');
    assert.equal(calls[0], 'http://localhost:3001/v1/mailbox/threads/thread-1?limit=25&offset=0');
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test('mailbox sync helpers use backend sync endpoints', async () => {
  const previousFetch = globalThis.fetch;
  const calls: string[] = [];

  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    calls.push(`${init?.method ?? 'GET'} ${String(input)}`);
    const payload =
      init?.method === 'POST'
        ? { status: 'queued', state: { connected: true, total_threads: 0 } }
        : { connected: true, total_threads: 0 };
    return Promise.resolve(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  }) as typeof fetch;

  try {
    const state = await getMailboxSyncState();
    const triggered = await triggerMailboxSync();

    assert.equal(state.connected, true);
    assert.equal(triggered.status, 'queued');
    assert.deepEqual(calls, [
      'GET http://localhost:3001/v1/mailbox/sync-state',
      'POST http://localhost:3001/v1/mailbox/sync',
    ]);
  } finally {
    globalThis.fetch = previousFetch;
  }
});
