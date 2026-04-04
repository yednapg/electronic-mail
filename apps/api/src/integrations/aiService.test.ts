import test from 'node:test';
import assert from 'node:assert/strict';

import { decideEntities } from './aiService';

const originalFetch = globalThis.fetch;

test.afterEach(() => {
  globalThis.fetch = originalFetch;
});

test('decideEntities returns parsed items from the ai service', async () => {
  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({
        items: [
          {
            id: 'entity-1',
            is_decision: true,
            title: 'Review the shared document',
            why_this_is_here: 'Rahul is waiting on your review.',
            primary_action: 'review',
            timing_band: 'today',
            importance_level: 'medium',
            action_confidence: 'high',
          },
        ],
      }),
      {
        status: 200,
        headers: {
          'content-type': 'application/json',
        },
      },
    );

  const items = await decideEntities([
    {
      id: 'entity-1',
      source: 'gmail',
      subject: 'Please review this doc',
      body: 'Can you review this before tomorrow?',
      sender: 'rahul@example.com',
      participants: ['rahul@example.com'],
      timestamp: '2026-04-03T06:00:00.000Z',
      due_at: '2026-04-04T06:00:00.000Z',
      thread_summary: 'Rahul wants your review.',
    },
  ]);

  assert.equal(items.length, 1);
  assert.equal(items[0].primary_action, 'review');
  assert.equal(items[0].title, 'Review the shared document');
});

test('decideEntities throws on non-OK ai service responses', async () => {
  globalThis.fetch = async () => new Response('bad gateway', { status: 502 });

  await assert.rejects(
    decideEntities([
      {
        id: 'entity-1',
        source: 'gmail',
        subject: 'Hello',
        body: 'Body',
        sender: 'sender@example.com',
        participants: [],
        timestamp: '2026-04-03T06:00:00.000Z',
        due_at: null,
        thread_summary: null,
      },
    ]),
    /Decision service request failed/,
  );
});
