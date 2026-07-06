import assert from 'node:assert/strict';
import test from 'node:test';

import { clearAppSessionCache, getCachedAppSession, refreshAppSession } from './app-session-store';
import type { AppSessionStateResponse, MailboxResponse } from './types';

test('app session cache switches by backend user id without carrying mailbox rows across users', async () => {
  const localStorage = makeLocalStorage();
  const userOne = makeSession('user-1', 'user-one-thread', 1);
  const userTwo = makeSession('user-2', 'user-two-thread', 0);
  const responses = [userOne, userTwo];
  const originalWindow = globalThis.window;
  const originalFetch = globalThis.fetch;

  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { localStorage },
  });
  globalThis.fetch = async () => new Response(JSON.stringify(responses.shift()), { status: 200 });

  try {
    clearAppSessionCache();
    const first = await refreshAppSession();
    assert.equal(first?.user.id, 'user-1');
    assert.equal(getCachedAppSession()?.mailbox.sections[0]?.rows[0]?.thread_id, 'user-one-thread');

    const second = await refreshAppSession();
    assert.equal(second?.user.id, 'user-2');
    assert.equal(second?.mailbox.total_threads, 0);
    assert.equal(getCachedAppSession()?.user.id, 'user-2');
    assert.equal(getCachedAppSession()?.mailbox.sections.length, 0);
    assert.notEqual(getCachedAppSession()?.mailbox.sections[0]?.rows[0]?.thread_id, 'user-one-thread');
  } finally {
    clearAppSessionCache();
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: originalWindow,
    });
    globalThis.fetch = originalFetch;
  }
});

function makeSession(userID: string, threadID: string, totalThreads: number): AppSessionStateResponse {
  return {
    user: { id: userID, email: `${userID}@example.com`, display_name: userID },
    readiness: {
      mode: 'returning',
      stage: 'ready',
      ready_to_enter: true,
      dashboard_ready: true,
      mailbox_ready: true,
      ready_dashboard_count: 0,
      ready_mail_group_count: totalThreads,
      full_import_running: false,
      full_import_completed: true,
      user_display_name: userID,
      error_message: null,
    },
    dashboard: {
      generated_at: '2026-07-03T00:00:00+00:00',
      profile: { email: `${userID}@example.com`, display_name: userID },
      auth: { available: true, connected: true, connect_url: null },
      briefing: { headline: '', brief: '' },
      feed: { now: [], today: [], worth_knowing: [] },
    },
    mailbox: makeMailbox(threadID, totalThreads),
    sync: {
      enrichment_pending_count: 0,
      ready_group_count: totalThreads,
      full_import_running: false,
      full_import_completed: true,
    },
  } as AppSessionStateResponse;
}

function makeMailbox(threadID: string, totalThreads: number): MailboxResponse {
  return {
    label: 'inbox',
    total_threads: totalThreads,
    loaded_threads: totalThreads,
    sections: totalThreads > 0
      ? [
        {
          id: 'today',
          title: 'Today',
          rows: [
            {
              thread_id: threadID,
              latest_source_record_id: `${threadID}-message`,
              latest_received_at: '2026-07-03T00:00:00+00:00',
              latest_subject: threadID,
              latest_sender: `${threadID}@example.com`,
              participants: [],
              message_count: 1,
              lifecycle_updates: [],
            },
          ],
        },
      ]
      : [],
  };
}

function makeLocalStorage() {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    getItem(key: string) {
      return store.get(key) ?? null;
    },
    setItem(key: string, value: string) {
      store.set(key, value);
    },
    removeItem(key: string) {
      store.delete(key);
    },
  };
}
