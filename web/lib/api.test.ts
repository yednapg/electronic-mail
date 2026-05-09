import test from 'node:test';
import assert from 'node:assert/strict';

import { getDashboard, getHistory, isDemoMode } from './api';

test('demo mode is opt-in so the app uses the real backend by default', () => {
  const previousMode = process.env.NEXT_PUBLIC_DEMO_MODE;

  delete process.env.NEXT_PUBLIC_DEMO_MODE;

  try {
    assert.equal(isDemoMode(), false);
    process.env.NEXT_PUBLIC_DEMO_MODE = 'true';
    assert.equal(isDemoMode(), true);
  } finally {
    if (previousMode === undefined) {
      delete process.env.NEXT_PUBLIC_DEMO_MODE;
    } else {
      process.env.NEXT_PUBLIC_DEMO_MODE = previousMode;
    }
  }
});

test('history fetcher returns hardcoded demo history without fetching backend', async () => {
  const previousMode = process.env.NEXT_PUBLIC_DEMO_MODE;
  const previousFetch = globalThis.fetch;

  process.env.NEXT_PUBLIC_DEMO_MODE = 'true';
  globalThis.fetch = (() => {
    throw new Error('Demo history should not fetch the backend');
  }) as typeof fetch;

  try {
    const history = await getHistory();

    assert.equal(history.total, 15);
    assert.equal(history.years[0].year, '2026');
    assert.equal(history.years[0].months[0].month, '2026-05');
    assert.equal(history.years[0].months[0].days[0].rows[0].source_record_id, 'gmail:pycon-us-invite-2026');
  } finally {
    if (previousMode === undefined) {
      delete process.env.NEXT_PUBLIC_DEMO_MODE;
    } else {
      process.env.NEXT_PUBLIC_DEMO_MODE = previousMode;
    }

    globalThis.fetch = previousFetch;
  }
});

test('history fetcher calls backend when demo mode is not enabled', async () => {
  const previousMode = process.env.NEXT_PUBLIC_DEMO_MODE;
  const previousFetch = globalThis.fetch;
  const calls: string[] = [];

  delete process.env.NEXT_PUBLIC_DEMO_MODE;
  globalThis.fetch = ((input: RequestInfo | URL) => {
    calls.push(String(input));
    return Promise.resolve(
      new Response(JSON.stringify({ total: 0, limit: 60, offset: 0, years: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  }) as typeof fetch;

  try {
    const history = await getHistory();

    assert.equal(history.total, 0);
    assert.equal(calls[0], 'http://localhost:3001/v1/history?limit=60&offset=0');
  } finally {
    if (previousMode === undefined) {
      delete process.env.NEXT_PUBLIC_DEMO_MODE;
    } else {
      process.env.NEXT_PUBLIC_DEMO_MODE = previousMode;
    }

    globalThis.fetch = previousFetch;
  }
});

test('dashboard fetcher returns hardcoded demo data without fetching backend', async () => {
  const previousMode = process.env.NEXT_PUBLIC_DEMO_MODE;
  const previousFetch = globalThis.fetch;

  process.env.NEXT_PUBLIC_DEMO_MODE = 'true';
  globalThis.fetch = (() => {
    throw new Error('Demo dashboard should not fetch the backend');
  }) as typeof fetch;

  try {
    const dashboard = await getDashboard();

    assert.equal(dashboard.auth.connected, true);
    assert.equal(dashboard.profile?.display_name, 'Gaurav Pandey');
    assert.match(dashboard.briefing?.brief ?? '', /📆 5 meetings/);
    assert.ok(dashboard.feed.now.length > 0);
    assert.ok(dashboard.feed.today.length > 0);
    assert.ok(dashboard.feed.worth_knowing.length > 0);
    assert.ok(dashboard.feed.now.some((item) => item.id === 'hdfc-card-bill'));
    assert.ok(dashboard.feed.today.some((item) => item.id === 'apple-replacement'));
  } finally {
    if (previousMode === undefined) {
      delete process.env.NEXT_PUBLIC_DEMO_MODE;
    } else {
      process.env.NEXT_PUBLIC_DEMO_MODE = previousMode;
    }

    globalThis.fetch = previousFetch;
  }
});

test('dashboard fetcher calls backend when demo mode is not enabled', async () => {
  const previousMode = process.env.NEXT_PUBLIC_DEMO_MODE;
  const previousFetch = globalThis.fetch;
  const calls: string[] = [];

  delete process.env.NEXT_PUBLIC_DEMO_MODE;
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
    if (previousMode === undefined) {
      delete process.env.NEXT_PUBLIC_DEMO_MODE;
    } else {
      process.env.NEXT_PUBLIC_DEMO_MODE = previousMode;
    }

    globalThis.fetch = previousFetch;
  }
});
