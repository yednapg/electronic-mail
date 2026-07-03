import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { buildCommandIndex, buildCommandIndexFromAppSession, filterCommands, getStaticCommands } from './command-index';
import type { AppSessionStateResponse, GmailViewResponse, SmartInboxResponse } from './types';

const commandIndexRouteSource = readFileSync(new URL('../app/api/command-index/route.ts', import.meta.url), 'utf8');
const commandPaletteSource = readFileSync(new URL('../components/command-palette/CommandPalette.tsx', import.meta.url), 'utf8');

test('gmail command entries use titles without exposing summaries as visible preview text', () => {
  const gmail: GmailViewResponse = {
    total_threads: 1,
    sections: [
      {
        id: 'today',
        title: 'Today',
        rows: [
          {
            thread_id: 'thread-order',
            entity_id: 'entity-order',
            latest_source_record_id: 'message-latest',
            latest_received_at: '2026-05-11T09:30:00+05:30',
            latest_subject: 'Your order update',
            latest_sender: 'Apple Store',
            participants: ['orders@fruitco.example', 'you@example.com'],
            message_count: 3,
            title: 'Apple order W123456789 is out for delivery today',
            summary: 'Apple shipped your order and says it will arrive today.',
            snippet: 'Your package is out for delivery.',
            current_state: 'waiting',
            lifecycle_state: 'active',
            outcome_type: null,
            lifecycle_updates: [],
          },
          {
            thread_id: 'thread-missing-title',
            entity_id: 'entity-missing-title',
            latest_source_record_id: 'message-missing-title',
            latest_received_at: '2026-05-11T09:45:00+05:30',
            latest_subject: null,
            latest_sender: 'Shipping Robot',
            participants: ['shipping@example.com'],
            message_count: 1,
            title: null,
            ai_title: null,
            summary: null,
            snippet: 'Body preview should stay hidden.',
            current_state: 'waiting',
            lifecycle_state: 'active',
            outcome_type: null,
            lifecycle_updates: [],
          },
        ],
      },
    ],
  };

  const command = buildCommandIndex(null, '2026-05-11T10:00:00.000Z', gmail)
    .commands
    .find((item) => item.id === 'email:thread-order');

  assert.ok(command);
  assert.match(command.title, /Apple order W123456789 is out for delivery today/);
  assert.equal(command.subtitle, 'Today - Open email');
  assert.doesNotMatch(command.subtitle, /Your package is out for delivery/);
  assert.doesNotMatch(command.searchText, /Your package is out for delivery/);
  assert.doesNotMatch(command.subtitle, /Apple shipped your order/);
  assert.doesNotMatch(command.searchText, /Apple shipped your order/);

  const missingTitleCommand = buildCommandIndex(null, '2026-05-11T10:00:00.000Z', gmail)
    .commands
    .find((item) => item.id === 'email:thread-missing-title');

  assert.ok(missingTitleCommand);
  assert.match(missingTitleCommand.title, /Untitled email/);
  assert.doesNotMatch(missingTitleCommand.title, /Body preview should stay hidden/);
  assert.doesNotMatch(missingTitleCommand.searchText, /Body preview should stay hidden/);
});

test('static command palette defaults are inbox-first', () => {
  const staticCommands = getStaticCommands();
  const defaultResults = filterCommands(staticCommands, '');

  assert.equal(defaultResults[0]?.id, 'nav:gmail');
  assert.equal(defaultResults[0]?.title, 'Inbox');
  assert.equal(defaultResults[0]?.href, '/gmail');
  assert.equal(defaultResults[1]?.title, 'To-do');
  assert.notEqual(defaultResults[1]?.title, 'Dashboard');
});

test('command index uses smart inbox rows from app session before raw mailbox rows', () => {
  const session = makeSession({
    smart_inbox: makeSmartInbox(),
    mailbox: {
      label: 'inbox',
      total_threads: 1,
      sections: [
        {
          id: 'today',
          title: 'Today',
          rows: [
            {
              thread_id: 'raw-thread',
              entity_id: 'raw-thread',
              latest_source_record_id: 'raw-message',
              latest_received_at: '2026-06-20T08:00:00Z',
              latest_subject: 'Raw Apple mail should not be indexed first',
              latest_sender: 'Apple <orders@fruitco.example>',
              participants: ['orders@fruitco.example'],
              message_count: 1,
              title: 'Raw Apple mail should not be indexed first',
              summary: 'Raw summary hidden.',
              snippet: 'Raw snippet hidden.',
              lifecycle_updates: [],
            },
          ],
        },
      ],
    },
  });

  const commands = buildCommandIndexFromAppSession(session, '2026-06-20T09:10:00Z').commands;

  const command = commands.find((item) => item.id === 'email:smart-row:smart-apple-order');
  assert.ok(command);
  assert.match(command.title, /Apple order W123456789 is out for delivery/);
  assert.equal(command.href, '/gmail/threads/smart-row%3Asmart-apple-order');
  assert.doesNotMatch(command.searchText, /summary must not appear/);
  assert.equal(commands.some((item) => item.id === 'email:raw-thread'), false);
});

test('command index route builds from app session rather than legacy gmail view', () => {
  assert.match(commandIndexRouteSource, /getAppSession/);
  assert.match(commandIndexRouteSource, /buildCommandIndexFromAppSession/);
  assert.doesNotMatch(commandIndexRouteSource, /getGmailView/);
});

test('command palette cache version changes for smart inbox command source', () => {
  assert.match(commandPaletteSource, /electronic-mail-command-index:v5/);
  assert.doesNotMatch(commandPaletteSource, /electronic-mail-command-index:v4/);
});

function makeSession(overrides: Partial<AppSessionStateResponse> = {}): AppSessionStateResponse {
  return {
    user: {
      id: 'user-1',
      email: 'me@example.com',
      first_name: 'TestUser',
      display_name: 'TestUser',
    },
    readiness: {
      mode: 'returning',
      stage: 'ready',
      ready_to_enter: true,
      dashboard_ready: true,
      mailbox_ready: true,
      ready_dashboard_count: 0,
      ready_mail_group_count: 1,
      full_import_running: false,
      full_import_completed: false,
      user_display_name: 'TestUser',
      error_message: null,
    },
    dashboard: {
      auth: { available: true, connected: true, connect_url: null },
      feed: {
        now: [],
        today: [],
        worth_knowing: [],
      },
      runtime_status: {},
    },
    mailbox: {
      label: 'inbox',
      total_threads: 0,
      sections: [],
    },
    sync: {
      enrichment_pending_count: 0,
      ready_group_count: 1,
      full_import_running: false,
      full_import_completed: false,
    },
    ...overrides,
  };
}

function makeSmartInbox(): SmartInboxResponse {
  return {
    total_rows: 1,
    sections: [
      {
        id: 'today',
        title: 'Today',
        rows: [
          {
            id: 'smart-apple-order',
            row_key: 'mail-object:apple-order-W123456789',
            row_type: 'verified_group',
            title: 'Apple order W123456789 is out for delivery',
            summary: 'This summary must not appear in command search.',
            primary_sender: 'Apple <orders@fruitco.example>',
            latest_message_at: '2026-06-20T09:00:00Z',
            latest_message_id: 'msg-apple-latest',
            reader_thread_id: null,
            source_thread_ids: ['thread-confirmed', 'thread-shipped'],
            source_message_ids: ['msg-confirmed', 'msg-apple-latest'],
            confidence_tier: 'exact',
            confidence: 1,
            grouping_reason: {},
            offline_status: 'ready',
            readiness: 'ready',
            action_type: 'track',
            priority: 90,
          },
        ],
      },
    ],
    related_suggestions: [],
    ready_count: 1,
    partial_count: 0,
    failed_count: 0,
    generated_at: '2026-06-20T09:10:00Z',
    hot_window_days: 30,
    hot_window_message_cap: 0,
    hot_window_thread_cap: 0,
  };
}
