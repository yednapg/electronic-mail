import test from 'node:test';
import assert from 'node:assert/strict';

import type { DashboardResponse, FeedItem } from './types';
import { buildCommandIndex, filterCommands, getStaticCommands } from './command-index';

function createDashboard(items: Partial<DashboardResponse['feed']> = {}): DashboardResponse {
  return {
    auth: {
      available: true,
      connected: true,
      connect_url: null,
    },
    profile: null,
    briefing: null,
    feed: {
      now: [],
      today: [],
      worth_knowing: [],
      ...items,
    },
  };
}

function createFeedItem(overrides: Partial<FeedItem> = {}): FeedItem {
  return {
    id: 'item-1',
    entity_id: 'entity-1',
    user_id: 'user-1',
    need_type: 'decision',
    action_type: 'external',
    effort_level: 'quick',
    timing_band: 'now',
    action_confidence: 'high',
    primary_action: 'reply',
    fallback_action: 'open',
    title: 'Reply to Airbnb refund request',
    why_this_is_here: 'Support needs one more screenshot.',
    source: 'gmail',
    trace_id: 'trace-1',
    created_at: '2026-04-03T04:00:00.000Z',
    ...overrides,
  };
}

test('static commands include the core app destinations', () => {
  assert.deepEqual(
    getStaticCommands().map((command) => command.href),
    ['/dashboard', '/gmail', '/history', '/raw-feed'],
  );
});

test('command index includes work and complete commands from current dashboard items', () => {
  const index = buildCommandIndex(
    createDashboard({
      now: [
        createFeedItem({
          id: 'hdfc-card-bill',
          entity_id: 'entity-hdfc-card-bill',
          title: 'HDFC credit card bill due today',
          primary_action: 'pay',
        }),
      ],
    }),
    '2026-05-11T00:00:00.000Z',
  );

  const workCommand = index.commands.find((command) => command.id === 'work:hdfc-card-bill');
  const completeCommand = index.commands.find((command) => command.id === 'complete:entity-hdfc-card-bill');

  assert.equal(workCommand?.href, '/entities/entity-hdfc-card-bill/thread');
  assert.equal(completeCommand?.action?.kind, 'complete-entity');
  assert.equal(completeCommand?.action?.entityId, 'entity-hdfc-card-bill');
});

test('command index skips calendar items and resolved completion actions', () => {
  const index = buildCommandIndex(
    createDashboard({
      now: [
        createFeedItem({
          id: 'scrum',
          entity_id: 'entity-scrum',
          source: 'calendar',
          need_type: 'awareness',
          primary_action: 'none',
          title: 'Scrum meeting with Team',
        }),
        createFeedItem({
          id: 'delivered-order',
          entity_id: 'entity-delivered-order',
          need_type: 'awareness',
          lifecycle_state: 'resolved',
          current_state: 'done',
          title: 'Your Samsung order was delivered.',
          primary_action: 'none',
        }),
      ],
    }),
  );

  assert.equal(index.commands.some((command) => command.id.includes('scrum')), false);
  assert.equal(index.commands.some((command) => command.id === 'work:delivered-order'), true);
  assert.equal(index.commands.some((command) => command.id === 'complete:entity-delivered-order'), false);
});

test('work commands without an entity fall back to the dashboard and cannot complete', () => {
  const index = buildCommandIndex(
    createDashboard({
      today: [
        createFeedItem({
          id: 'local-work',
          entity_id: '',
          title: 'Local unsynced task',
        }),
      ],
    }),
  );

  assert.equal(index.commands.find((command) => command.id === 'work:local-work')?.href, '/dashboard');
  assert.equal(index.commands.some((command) => command.id === 'complete:'), false);
});

test('search ranking favors matching work and done-specific action commands', () => {
  const index = buildCommandIndex(
    createDashboard({
      now: [
        createFeedItem({
          id: 'hdfc-card-bill',
          entity_id: 'entity-hdfc-card-bill',
          title: 'HDFC credit card bill due today',
          primary_action: 'pay',
        }),
      ],
    }),
  );

  assert.equal(filterCommands(index.commands, 'hdfc')[0].id, 'work:hdfc-card-bill');
  assert.equal(filterCommands(index.commands, 'done hdfc')[0].id, 'complete:entity-hdfc-card-bill');
});
