/** Unit tests for dashboard copy shaping and agenda extraction. */
import test from 'node:test';
import assert from 'node:assert/strict';

import type { FeedItem, FeedResponse } from '../../lib/types';
import { buildAgenda, buildSections, buildSummary, toActionSentence, toSectionCta } from './page';

function createFeedItem(overrides: Partial<FeedItem> = {}): FeedItem {
  return {
    id: 'item-1',
    entity_id: 'entity-1',
    user_id: 'user-1',
    need_type: 'decision',
    action_type: 'external',
    effort_level: 'quick',
    timing_band: 'today',
    action_confidence: 'medium',
    primary_action: 'open',
    fallback_action: 'open',
    title: 'Vendor security questionnaire',
    why_this_is_here: 'This needs your attention.',
    trace_id: 'trace-1',
    created_at: '2026-04-03T04:00:00.000Z',
    ...overrides,
  };
}

function createFeed(overrides: Partial<FeedResponse> = {}): FeedResponse {
  return {
    now: [],
    today: [],
    worth_knowing: [],
    ...overrides,
  };
}

test('agenda sorts calendar items chronologically', () => {
  const earlyMeeting = createFeedItem({
    id: 'meeting-early',
    entity_id: 'meeting-early',
    source: 'calendar',
    due_at: '2026-04-03T10:00:00+05:30',
    title: 'Daily standup',
    primary_action: 'none',
  });
  const laterMeeting = createFeedItem({
    id: 'meeting-late',
    entity_id: 'meeting-late',
    source: 'calendar',
    due_at: '2026-04-03T14:00:00+05:30',
    title: 'Design review',
    primary_action: 'none',
  });

  const agenda = buildAgenda(
    createFeed({
      today: [laterMeeting, earlyMeeting],
    }),
  );

  assert.deepEqual(
    agenda.map((item) => item.id),
    ['meeting-early', 'meeting-late'],
  );
  assert.equal(agenda[0].time, '10:00');
  assert.equal(agenda[1].time, '14:00');
});

test('agenda keeps all-day items visible', () => {
  const allDayEvent = createFeedItem({
    id: 'holiday',
    entity_id: 'holiday',
    source: 'calendar',
    due_at: '2026-04-03',
    title: 'Good Friday',
    primary_action: 'none',
  });

  const agenda = buildAgenda(
    createFeed({
      today: [allDayEvent],
    }),
  );

  assert.equal(agenda.length, 1);
  assert.equal(agenda[0].time, 'All day');
  assert.equal(agenda[0].title, 'Good Friday');
});

test('action-first titles are preserved without duplicate prefixing', () => {
  const item = createFeedItem({
    title: 'Review the vendor security questionnaire',
    primary_action: 'review',
  });

  assert.equal(toActionSentence(item), 'Review the vendor security questionnaire');
});

test('register action becomes an action-first sentence when title is not already action-first', () => {
  const item = createFeedItem({
    title: 'YC Startup School India talk',
    primary_action: 'register',
  });

  assert.equal(toActionSentence(item), 'Register for YC Startup School India talk');
});

test('natural status titles are preserved without adding vague action prefixes', () => {
  const item = createFeedItem({
    title: 'Northstar Bank declined a recurring card payment as non-compliant.',
    primary_action: 'review',
  });

  assert.equal(toActionSentence(item), 'Northstar Bank declined a recurring card payment as non-compliant.');
});

test('awareness items keep their natural title', () => {
  const item = createFeedItem({
    need_type: 'awareness',
    primary_action: 'none',
    title: 'Your Samsung order #12304086779 was delivered.',
  });

  assert.equal(toActionSentence(item), 'Your Samsung order #12304086779 was delivered.');
});

test('dashboard renders a Worth Knowing section for low-priority overflow items', () => {
  const sections = buildSections(
    createFeed({
      worth_knowing: [
        createFeedItem({
          id: 'context-1',
          entity_id: 'context-1',
          timing_band: 'later',
          title: 'FYI: deployment completed',
          primary_action: 'none',
        }),
      ],
    }),
  );

  assert.equal(sections[2].title, 'Worth Knowing');
  assert.equal(sections[2].id, 'worth-knowing');
  assert.equal(sections[2].items.length, 1);
});

test('dashboard summary renders backend-generated briefing copy without rewriting it', () => {
  const summary = buildSummary({
    briefing: {
      headline: 'Good morning, TestUser.',
      brief: 'You have 0 meetings and a mostly open afternoon.',
    },
  });

  assert.deepEqual(summary, {
    headline: 'Good morning, TestUser.',
    brief: 'You have 0 meetings and a mostly open afternoon.',
  });
});

test('gmail items expose an explicit archive CTA when one thread can be mutated', () => {
  const cta = toSectionCta(
    createFeedItem({
      source: 'gmail',
      gmail_thread_id: 'thread-123',
      gmail_thread_action: 'archive',
    }),
  );

  assert.deepEqual(cta, {
    label: 'Archive',
    tone: 'green',
    action: {
      kind: 'gmail-thread',
      threadId: 'thread-123',
      operation: 'archive',
    },
  });
});
