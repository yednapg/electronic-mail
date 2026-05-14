import assert from 'node:assert/strict';
import test from 'node:test';

import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { GmailView, formatGmailReceivedAt } from './page';
import type { GmailViewResponse } from '../../lib/types';

test('gmail received time stays compact', () => {
  assert.match(formatGmailReceivedAt('2026-05-11T09:30:00'), /9:30/);
});

test('gmail view renders raw thread buckets before lifecycle grouping', () => {
  const gmail: GmailViewResponse = {
    total_threads: 2,
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
            latest_subject: 'Order shipped',
            latest_sender: 'Apple Store',
            participants: ['orders@apple.com', 'you@example.com'],
            message_count: 3,
            summary: 'Apple shipped your order.',
            snippet: 'Your order is on the way.',
            current_state: 'waiting',
            lifecycle_state: 'active',
            outcome_type: null,
            lifecycle_updates: [
              {
                source_record_id: 'message-confirmed',
                received_at: '2026-05-10T09:30:00+05:30',
                subject: 'Order confirmed',
                sender: 'Apple Store',
                summary: 'Apple confirmed the order.',
              },
              {
                source_record_id: 'message-latest',
                received_at: '2026-05-11T09:30:00+05:30',
                subject: 'Order shipped',
                sender: 'Apple Store',
                summary: 'Apple shipped the order.',
              },
            ],
          },
        ],
      },
      {
        id: 'last-seven-days',
        title: 'Last seven days',
        rows: [
          {
            thread_id: 'thread-bank',
            entity_id: null,
            latest_source_record_id: 'message-bank',
            latest_received_at: '2026-05-08T11:00:00+05:30',
            latest_subject: 'Statement ready',
            latest_sender: 'Bank',
            participants: [],
            message_count: 1,
            summary: null,
            snippet: 'Your statement is ready.',
            current_state: 'open',
            lifecycle_state: 'active',
            outcome_type: null,
            lifecycle_updates: [
              {
                source_record_id: 'message-bank',
                received_at: '2026-05-08T11:00:00+05:30',
                subject: 'Statement ready',
                sender: 'Bank',
                summary: 'Your statement is ready.',
              },
            ],
          },
        ],
      },
    ],
  };

  const markup = renderToStaticMarkup(React.createElement(GmailView, { gmail }));

  assert.match(markup, /Gmail/);
  assert.match(markup, /Today/);
  assert.match(markup, /Last seven days/);
  assert.match(markup, /Order shipped/);
  assert.match(markup, /3 emails/);
  assert.match(markup, /Apple shipped your order/);
  assert.match(markup, /Apple confirmed the order/);
  assert.match(markup, /Statement ready/);
  assert.doesNotMatch(markup, /Archive|Unarchive/);
});
