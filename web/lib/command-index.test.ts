import test from 'node:test';
import assert from 'node:assert/strict';

import type { DashboardResponse, FeedItem, GmailViewResponse } from './types';
import { buildCommandIndex, buildGmailCommands, filterCommands, getStaticCommands } from './command-index';

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

function createGmailView(): GmailViewResponse {
  return {
    total_threads: 1,
    sections: [
      {
        id: 'today',
        title: 'Today',
        rows: [
          {
            thread_id: 'thread-northstar',
            entity_id: 'entity-northstar',
            latest_source_record_id: 'source-northstar',
            latest_received_at: '2026-05-13T09:30:00+05:30',
            latest_subject: 'Credit card bill due today',
            latest_sender: 'Northstar Bank <alerts@northstarbank.example>',
            participants: ['alerts@northstarbank.example', 'demo@example.test'],
            message_count: 1,
            summary: 'Please pay before 5 PM to avoid late fees.',
            snippet: 'Please pay before 5 PM.',
            current_state: 'open',
            lifecycle_state: 'active',
            outcome_type: null,
            lifecycle_updates: [],
          },
        ],
      },
    ],
  };
}

test('static commands include the core app destinations', () => {
  assert.deepEqual(
    getStaticCommands().map((command) => command.href),
    ['/dashboard', '/gmail'],
  );
});

test('empty command palette results show only top-level navigation', () => {
  const index = buildCommandIndex(
    createDashboard({
      now: [
        createFeedItem({
          id: 'northstar-card-bill',
          entity_id: 'entity-northstar-card-bill',
          title: 'Northstar credit card bill due today',
          primary_action: 'pay',
        }),
      ],
    }),
    '2026-05-13T00:00:00.000Z',
    createGmailView(),
  );

  assert.deepEqual(
    filterCommands(index.commands, '').map((command) => command.id),
    ['nav:dashboard', 'nav:gmail'],
  );
});

test('command index includes work and complete commands from current dashboard items', () => {
  const index = buildCommandIndex(
    createDashboard({
      now: [
        createFeedItem({
          id: 'northstar-card-bill',
          entity_id: 'entity-northstar-card-bill',
          title: 'Northstar credit card bill due today',
          primary_action: 'pay',
        }),
      ],
    }),
    '2026-05-11T00:00:00.000Z',
  );

  const workCommand = index.commands.find((command) => command.id === 'work:northstar-card-bill');
  const completeCommand = index.commands.find((command) => command.id === 'complete:entity-northstar-card-bill');

  assert.equal(workCommand?.href, '/entities/entity-northstar-card-bill/thread');
  assert.equal(completeCommand?.action?.kind, 'complete-entity');
  assert.equal(completeCommand?.action?.entityId, 'entity-northstar-card-bill');
});

test('command index includes real Gmail thread jumps for cmd-k inbox search', () => {
  const index = buildCommandIndex(createDashboard(), '2026-05-13T00:00:00.000Z', createGmailView());
  const emailCommand = index.commands.find((command) => command.id === 'email:thread-northstar');

  assert.equal(emailCommand?.kind, 'email');
  assert.equal(emailCommand?.title, 'Northstar Bank: Credit card bill due today');
  assert.equal(emailCommand?.href, '/gmail/threads/thread-northstar');
  assert.equal(filterCommands(index.commands, 'northstar bill')[0].id, 'email:thread-northstar');
});

test('gmail commands open rows even before entity derivation finishes', () => {
  const gmail = createGmailView();
  const commands = buildGmailCommands({
    ...gmail,
    sections: [
      {
        ...gmail.sections[0],
        rows: [{ ...gmail.sections[0].rows[0], entity_id: null }],
      },
    ],
  });

  assert.equal(commands.length, 1);
  assert.equal(commands[0].href, '/gmail/threads/thread-northstar');
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

test('work commands prefer direct Gmail thread links when available', () => {
  const index = buildCommandIndex(
    createDashboard({
      now: [
        createFeedItem({
          id: 'email-work',
          entity_id: 'entity-work',
          gmail_thread_id: 'thread-work',
        }),
      ],
    }),
  );

  assert.equal(index.commands.find((command) => command.id === 'work:email-work')?.href, '/gmail/threads/thread-work');
});

test('search ranking favors matching work and done-specific action commands', () => {
  const index = buildCommandIndex(
    createDashboard({
      now: [
        createFeedItem({
          id: 'northstar-card-bill',
          entity_id: 'entity-northstar-card-bill',
          title: 'Northstar credit card bill due today',
          primary_action: 'pay',
        }),
      ],
    }),
  );

  assert.equal(filterCommands(index.commands, 'northstar')[0].id, 'work:northstar-card-bill');
  assert.equal(filterCommands(index.commands, 'done northstar')[0].id, 'complete:entity-northstar-card-bill');
});
