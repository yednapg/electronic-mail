import assert from 'node:assert/strict';
import test from 'node:test';

import { mergeAppSession } from './app-session-store';
import type { AppSessionStateResponse, SmartInboxResponse } from './types';

test('app session accepts deferred dashboard snapshots instead of resurrecting stale to-do data', () => {
  const current = makeSession({
    dashboard: {
      auth: { available: true, connected: true, connect_url: null },
      feed: {
        now: [makeFeedItem('stale-now')],
        today: [],
        worth_knowing: [],
      },
      runtime_status: { feed_source: 'live' },
    },
  });
  const next = makeSession({
    dashboard: {
      auth: { available: true, connected: true, connect_url: null },
      feed: {
        now: [],
        today: [],
        worth_knowing: [],
      },
      runtime_status: { feed_source: 'deferred' },
    },
  });

  const merged = mergeAppSession(current, next);

  assert.equal(merged.dashboard.runtime_status?.feed_source, 'deferred');
  assert.equal(merged.dashboard.feed.now.length, 0);
});

test('app session still preserves non-empty mailbox rows across transient empty inbox snapshots', () => {
  const current = makeSession({
    mailbox: {
      label: 'inbox',
      total_threads: 2,
      loaded_threads: 2,
      sections: [
        {
          id: 'today',
          title: 'Today',
          rows: [makeMailboxRow('thread-1')],
        },
      ],
    },
  });
  const next = makeSession({
    mailbox: {
      label: 'inbox',
      total_threads: 0,
      loaded_threads: 0,
      sections: [],
    },
  });

  const merged = mergeAppSession(current, next);

  assert.equal(merged.mailbox.total_threads, 2);
  assert.equal(merged.mailbox.sections[0]?.rows[0]?.thread_id, 'thread-1');
});

test('app session preserves non-empty smart inbox rows across transient empty snapshots', () => {
  const current = makeSession({
    smart_inbox: makeSmartInbox('smart-row-1'),
  });
  const next = makeSession({
    smart_inbox: {
      ...makeSmartInbox('empty-smart-row'),
      total_rows: 0,
      sections: [],
      ready_count: 0,
    },
  });

  const merged = mergeAppSession(current, next);

  assert.equal(merged.smart_inbox?.total_rows, 1);
  assert.equal(merged.smart_inbox?.sections[0]?.rows[0]?.id, 'smart-row-1');
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
      ready_dashboard_count: 1,
      ready_mail_group_count: 2,
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
      total_threads: 2,
      loaded_threads: 2,
      sections: [
        {
          id: 'today',
          title: 'Today',
          rows: [makeMailboxRow('thread-1')],
        },
      ],
    },
    sync: {
      enrichment_pending_count: 0,
      ready_group_count: 2,
      full_import_running: false,
      full_import_completed: false,
    },
    ...overrides,
  };
}

function makeSmartInbox(rowId: string): SmartInboxResponse {
  return {
    total_rows: 1,
    sections: [
      {
        id: 'today',
        title: 'Today',
        rows: [
          {
            id: rowId,
            row_key: 'mail-object:apple-order-W123456789',
            row_type: 'verified_group',
            title: 'Apple order W123456789 is out for delivery',
            summary: 'Hidden summary',
            primary_sender: 'Apple <orders@fruitco.example>',
            latest_message_at: '2026-06-20T09:00:00Z',
            latest_message_id: 'msg-apple-latest',
            reader_thread_id: `smart-row:${rowId}`,
            source_thread_ids: ['thread-apple-confirmed', 'thread-apple-shipped'],
            source_message_ids: ['msg-apple-confirmed', 'msg-apple-latest'],
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

function makeFeedItem(id: string) {
  return {
    id,
    entity_id: id,
    user_id: 'user-1',
    source: 'gmail',
    need_type: 'decision',
    action_type: 'inline',
    effort_level: 'quick',
    timing_band: 'now',
    action_confidence: 'high',
    title: 'Stale dashboard item',
    why_this_is_here: 'Regression fixture for stale dashboard cache replacement.',
    summary: 'This should not survive a deferred dashboard snapshot.',
    primary_action: 'open',
    fallback_action: 'open',
    dashboard_visible: true,
    trace_id: 'trace-stale-now',
    created_at: '2026-06-20T08:00:00Z',
  } as const;
}

function makeMailboxRow(threadId: string) {
  return {
    thread_id: threadId,
    entity_id: threadId,
    latest_source_record_id: 'msg-1',
    latest_received_at: '2026-06-20T08:00:00Z',
    latest_subject: 'Apple order W123456789 is out for delivery',
    latest_sender: 'Apple <orders@fruitco.example>',
    participants: ['orders@fruitco.example'],
    message_count: 2,
    title: 'Apple order W123456789 is out for delivery',
    summary: '',
    snippet: 'Arriving today.',
    lifecycle_updates: [],
  };
}
