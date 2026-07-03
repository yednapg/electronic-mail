import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { GmailView, formatGmailReceivedAt } from './page';
import type { GmailViewResponse } from '../../lib/types';

const gmailThreadPageSource = readFileSync(new URL('./threads/[threadId]/page.tsx', import.meta.url), 'utf8');
const gmailInboxClientSource = readFileSync(new URL('./GmailInboxClient.tsx', import.meta.url), 'utf8');

test('gmail received time stays compact', () => {
  assert.match(
    formatGmailReceivedAt('2026-05-11T09:30:00+05:30', new Date('2026-05-11T10:00:00+05:30')),
    /9:30/,
  );
  assert.equal(
    formatGmailReceivedAt('2026-05-08T11:00:00+05:30', new Date('2026-05-11T10:00:00+05:30')),
    'May 8',
  );
});

test('gmail view renders clean mail-group rows without duplicating summaries', () => {
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

  assert.match(markup, /Inbox/);
  assert.match(markup, /Today/);
  assert.match(markup, /Last seven days/);
  assert.match(markup, /Order shipped/);
  assert.match(markup, /href="\/gmail\/threads\/thread-order"/);
  assert.match(markup, /href="\/gmail\/threads\/thread-bank"/);
  assert.match(markup, /Statement ready/);
  assert.doesNotMatch(markup, /Apple shipped your order/);
  assert.doesNotMatch(markup, /Archive|Unarchive/);
  assert.doesNotMatch(markup, /Apple confirmed the order/);
  assert.doesNotMatch(markup, /App settings|Worth Knowing|Calendar/);
});

test('gmail thread route uses the mounted mailbox client instead of server thread fetching', () => {
  assert.match(gmailThreadPageSource, /\bGmailInboxClient\b/);
  assert.doesNotMatch(gmailThreadPageSource, /\bgetMailboxThread\b/);
  assert.doesNotMatch(gmailThreadPageSource, /\bThreadDetail\b/);
});

test('gmail background processing copy does not imply first-run grouping is blocking', () => {
  assert.match(gmailInboxClientSource, /mailboxFromSmartInbox\(session\?\.smart_inbox/);
  assert.match(gmailInboxClientSource, /Importing older mail in background/);
  assert.match(gmailInboxClientSource, /Finishing AI titles for older mail/);
  assert.match(gmailInboxClientSource, /useActiveMailboxSync\(Boolean\(session\?\.user\.id\)\)/);
  assert.doesNotMatch(gmailInboxClientSource, /useActiveMailboxSync\(Boolean\(session\?\.dashboard\.auth\.connected\)\)/);
  assert.doesNotMatch(gmailInboxClientSource, /Processing older mail: /);
  assert.doesNotMatch(gmailInboxClientSource, /groups left/);
  assert.doesNotMatch(gmailInboxClientSource, /prefetch\('\/dashboard'\)/);
});

test('gmail thread reader requests summaries only after an explicit action', () => {
  assert.match(gmailInboxClientSource, /Generate summary/);
  assert.match(gmailInboxClientSource, /include_summary/);
  assert.match(gmailInboxClientSource, /summaryRequestedThreadIds/);
  assert.doesNotMatch(gmailInboxClientSource, /row\?\.summary\?\.trim\(\) \|\| row\?\.snippet/);
  assert.doesNotMatch(gmailInboxClientSource, /const body = row\.summary/);
});
