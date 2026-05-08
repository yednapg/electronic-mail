import test from 'node:test';
import assert from 'node:assert/strict';

import { getDashboard, getHistory, isDemoMode } from './api';

test('demo mode is enabled by default on the demo branch', () => {
  const previousMode = process.env.NEXT_PUBLIC_DEMO_MODE;

  delete process.env.NEXT_PUBLIC_DEMO_MODE;

  try {
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

  delete process.env.NEXT_PUBLIC_DEMO_MODE;
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

test('dashboard fetcher returns hardcoded demo data without fetching backend', async () => {
  const previousMode = process.env.NEXT_PUBLIC_DEMO_MODE;
  const previousFetch = globalThis.fetch;

  delete process.env.NEXT_PUBLIC_DEMO_MODE;
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
