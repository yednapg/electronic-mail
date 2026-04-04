import test from 'node:test';
import assert from 'node:assert/strict';

import type { FeedItem, FeedResponse } from '../../lib/types';
import { buildAgenda, toActionSentence } from './page';

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
