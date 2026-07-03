import assert from 'node:assert/strict';
import test from 'node:test';

import { mailboxFromSmartInbox, smartRowReaderThreadId } from './smart-inbox-view';
import type { MailboxResponse, SmartInboxResponse, SmartInboxRow } from './types';

test('smart inbox rows become the primary web inbox rows without summary previews', () => {
  const mailbox = mailboxFromSmartInbox(makeSmartInbox(), makeFallbackMailbox());

  assert.equal(mailbox?.total_threads, 1);
  assert.equal(mailbox?.window_days, 30);
  assert.equal(mailbox?.sections[0]?.rows[0]?.thread_id, 'smart-row:smart-apple-order');
  assert.equal(mailbox?.sections[0]?.rows[0]?.title, 'Apple order W123456789 is out for delivery');
  assert.equal(mailbox?.sections[0]?.rows[0]?.latest_sender, 'Apple <orders@apple.com>');
  assert.equal(mailbox?.sections[0]?.rows[0]?.message_count, 2);
  assert.equal(mailbox?.sections[0]?.rows[0]?.summary, null);
  assert.equal(mailbox?.sections[0]?.rows[0]?.ai_summary, null);
  assert.equal(mailbox?.sections[0]?.rows[0]?.snippet, null);
  assert.equal(mailbox?.sections[0]?.rows[0]?.presentation_status, 'ai_ready');
});

test('smart inbox falls back to raw mailbox when no smart rows exist yet', () => {
  const fallback = makeFallbackMailbox();
  const mailbox = mailboxFromSmartInbox({ ...makeSmartInbox(), total_rows: 0, sections: [] }, fallback);

  assert.equal(mailbox, fallback);
});

test('smart row reader ids match backend smart-row detail routing for grouped work', () => {
  assert.equal(smartRowReaderThreadId(makeSmartRow()), 'smart-row:smart-apple-order');
  assert.equal(
    smartRowReaderThreadId({
      ...makeSmartRow(),
      id: 'single-thread-row',
      row_key: 'thread-apple',
      row_type: 'normal',
      reader_thread_id: null,
      source_thread_ids: ['thread-apple'],
      source_message_ids: ['msg-apple-latest'],
    }),
    'thread-apple',
  );
});

function makeSmartInbox(): SmartInboxResponse {
  return {
    total_rows: 1,
    sections: [
      {
        id: 'today',
        title: 'Today',
        rows: [makeSmartRow()],
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

function makeSmartRow(): SmartInboxRow {
  return {
    id: 'smart-apple-order',
    row_key: 'mail-object:apple-order-W123456789',
    row_type: 'verified_group',
    title: 'Apple order W123456789 is out for delivery',
    summary: 'This summary must not appear in the inbox list.',
    primary_sender: 'Apple <orders@apple.com>',
    latest_message_at: '2026-06-20T09:00:00Z',
    latest_message_id: 'msg-apple-latest',
    reader_thread_id: null,
    source_thread_ids: ['thread-apple-confirmed', 'thread-apple-shipped'],
    source_message_ids: ['msg-apple-confirmed', 'msg-apple-latest'],
    confidence_tier: 'exact',
    confidence: 1,
    grouping_reason: { reference: 'W123456789' },
    offline_status: 'ready',
    readiness: 'ready',
    action_type: 'track',
    priority: 90,
  };
}

function makeFallbackMailbox(): MailboxResponse {
  return {
    label: 'inbox',
    total_threads: 1,
    loaded_threads: 1,
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
            latest_subject: 'Raw Apple email',
            latest_sender: 'Apple <orders@apple.com>',
            participants: ['Apple <orders@apple.com>'],
            message_count: 1,
            title: 'Raw Apple email',
            summary: 'Raw summary',
            snippet: 'Raw snippet',
            lifecycle_updates: [],
          },
        ],
      },
    ],
  };
}
