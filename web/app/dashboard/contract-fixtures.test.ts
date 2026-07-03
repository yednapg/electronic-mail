/** Contract fixture smoke tests for the web dashboard. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import type { DashboardResponse, GoogleAuthState, ThreadReaderResponse } from '../../lib/types';
import { buildSections, buildSummary } from './page';

const dashboardClientSource = fs.readFileSync(new URL('./DashboardClient.tsx', import.meta.url), 'utf8');

function fixturePath(name: string): string {
  let current = process.cwd();

  for (let depth = 0; depth < 5; depth += 1) {
    const candidate = path.join(current, 'contracts', 'fixtures', name);
    if (fs.existsSync(candidate)) {
      return candidate;
    }

    current = path.dirname(current);
  }

  throw new Error(`Could not locate contract fixture ${name}`);
}

function readFixture<T>(name: string): T {
  return JSON.parse(fs.readFileSync(fixturePath(name), 'utf8')) as T;
}

test('dashboard fixture maps to dashboard view models', () => {
  const dashboard = readFixture<DashboardResponse>('dashboard.json');

  assert.equal(dashboard.auth.connected, true);
  assert.equal(buildSummary(dashboard).headline, 'Good morning.');

  const sections = buildSections(dashboard.feed);
  assert.equal(sections[0].items[0].id, 'item-1');
  assert.equal(sections[0].items[0].cta, undefined);
  const worthKnowing = sections.find((section) => section.id === 'worth-knowing');

  assert.equal(worthKnowing?.items[0].id, 'item-2');
});

test('dashboard keeps mailbox sync active from signed-in session identity', () => {
  assert.match(dashboardClientSource, /useActiveMailboxSync\(Boolean\(session\?\.user\.id\)\)/);
  assert.doesNotMatch(dashboardClientSource, /useActiveMailboxSync\(Boolean\(session\?\.dashboard\.auth\.connected\)\)/);
});

test('auth fixture matches shared TypeScript contracts', () => {
  const auth = readFixture<GoogleAuthState>('google-auth-state.json');

  assert.equal(auth.connected, true);
});

test('thread reader fixture includes persisted source evidence', () => {
  const thread = readFixture<ThreadReaderResponse>('thread-reader.json');

  assert.equal(thread.entity_id, 'entity-1');
  assert.equal(thread.messages[0].source, 'gmail');
  assert.match(thread.messages[0].body, /Pay before 5 PM/);
  assert.match(thread.messages[0].snippet ?? '', /Autopay is not enabled/);
});
