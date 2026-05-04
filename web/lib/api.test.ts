import test from 'node:test';
import assert from 'node:assert/strict';

import { getDashboard, isDemoMode } from './api';

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
    assert.match(dashboard.briefing?.brief ?? '', /📆 3 meetings/);
    assert.equal(dashboard.feed.now.length, 8);
  } finally {
    if (previousMode === undefined) {
      delete process.env.NEXT_PUBLIC_DEMO_MODE;
    } else {
      process.env.NEXT_PUBLIC_DEMO_MODE = previousMode;
    }

    globalThis.fetch = previousFetch;
  }
});
