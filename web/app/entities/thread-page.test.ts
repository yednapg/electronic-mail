import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import type { ThreadReaderResponse } from '../../lib/types';
import { ThreadDetail } from './[entityId]/thread/page';

const threadPageSource = readFileSync(new URL('./[entityId]/thread/page.tsx', import.meta.url), 'utf8');

const thread: ThreadReaderResponse = {
  entity_id: 'entity-1',
  user_id: 'local-user',
  source: 'gmail',
  gmail_thread_id: 'thread-1',
  subject: 'HDFC credit card statement due today',
  total_messages: 1,
  limit: 25,
  offset: 0,
  has_more: false,
  messages: [
    {
      id: 'source-1',
      source: 'gmail',
      thread_id: 'thread-1',
      from_address: 'alerts@hdfcbank.net',
      to: 'gaurav@example.com',
      subject: 'HDFC credit card statement due today',
      body: 'Your card statement is due today. Autopay is not enabled. Pay before 5 PM to avoid late fees.',
      snippet: 'Autopay is not enabled for this card. Please pay before 5 PM.',
      label_ids: ['INBOX'],
      received_at: '2026-04-24T10:00:00+00:00',
    },
  ],
};

test('thread detail renders persisted subject body snippet and source', () => {
  const html = renderToStaticMarkup(React.createElement(ThreadDetail, { entityId: 'entity-1', thread, errorMessage: null }));

  assert.match(html, /HDFC credit card statement due today/);
  assert.match(html, /Autopay is not enabled for this card/);
  assert.match(html, /Pay before 5 PM/);
  assert.match(html, /Gmail/);
  assert.match(html, /alerts@hdfcbank.net/);
});

test('thread page auth check does not fetch the full dashboard', () => {
  assert.doesNotMatch(threadPageSource, /\bgetDashboard\b/);
  assert.match(threadPageSource, /\bgetGoogleAuthState\b/);
});

test('thread detail does not expose Gmail mutation actions or backend ids', () => {
  const html = renderToStaticMarkup(React.createElement(ThreadDetail, { entityId: 'entity-1', thread, errorMessage: null }));

  assert.doesNotMatch(html, /Archive/);
  assert.doesNotMatch(html, /Unarchive/);
  assert.doesNotMatch(html, /Mark read/);
  assert.doesNotMatch(html, /Entity:/);
  assert.doesNotMatch(html, /thread-1/);
});

test('thread detail renders focused page with more link', () => {
  const pageThread = {
    ...thread,
    total_messages: 100,
    limit: 25,
    offset: 0,
    has_more: true,
    messages: Array.from({ length: 25 }, (_, index) => ({
      ...thread.messages[0],
      id: `source-${index + 1}`,
      received_at: `2026-04-24T10:${String(index).padStart(2, '0')}:00+00:00`,
    })),
  };

  const html = renderToStaticMarkup(
    React.createElement(ThreadDetail, {
      entityId: 'entity-1',
      page: { limit: 25, offset: 0 },
      thread: pageThread,
      errorMessage: null,
    }),
  );

  assert.match(html, /href="\/entities\/entity-1\/thread\?limit=25&amp;offset=25"/);
  assert.match(html, />More</);
  assert.doesNotMatch(html, />Previous</);
});

test('thread detail renders previous and more links for middle pages', () => {
  const pageThread = {
    ...thread,
    total_messages: 100,
    limit: 25,
    offset: 25,
    has_more: true,
  };

  const html = renderToStaticMarkup(
    React.createElement(ThreadDetail, {
      entityId: 'entity-1',
      page: { limit: 25, offset: 25 },
      thread: pageThread,
      errorMessage: null,
    }),
  );

  assert.match(html, /href="\/entities\/entity-1\/thread\?limit=25&amp;offset=0"/);
  assert.match(html, /href="\/entities\/entity-1\/thread\?limit=25&amp;offset=50"/);
  assert.match(html, />Previous</);
  assert.match(html, />More</);
});
