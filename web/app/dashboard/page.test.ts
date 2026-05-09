/** Unit tests for dashboard copy shaping and agenda extraction. */
import test from 'node:test';
import assert from 'node:assert/strict';

import type { FeedItem, FeedResponse } from '../../lib/types';
import { buildAgenda, buildSections, buildSummary, toActionSentence, toSectionCta, toSectionDetail } from './page';

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
    title: 'Your Samsung order #10000000003 was delivered.',
  });

  assert.equal(toActionSentence(item), 'Your Samsung order #10000000003 was delivered.');
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

  const worthKnowing = sections.find((section) => section.id === 'worth-knowing');

  assert.equal(worthKnowing?.title, 'Worth Knowing');
  assert.equal(worthKnowing?.items.length, 1);
});

test('dashboard renders all work buckets when inbox items exist in each one', () => {
  const sections = buildSections(
    createFeed({
      now: [
        createFeedItem({
          id: 'now-work',
          entity_id: 'now-work',
          source: 'gmail',
          timing_band: 'now',
          title: 'Northstar credit card bill due today',
          primary_action: 'pay',
        }),
      ],
      today: [
        createFeedItem({
          id: 'today-work',
          entity_id: 'today-work',
          source: 'gmail',
          timing_band: 'today',
          title: 'Vendor security questionnaire',
          primary_action: 'review',
        }),
      ],
      worth_knowing: [
        createFeedItem({
          id: 'later-context',
          entity_id: 'later-context',
          source: 'gmail',
          timing_band: 'later',
          need_type: 'awareness',
          title: 'Your Samsung order #10000000003 was delivered.',
          primary_action: 'none',
        }),
      ],
    }),
  );

  assert.deepEqual(
    sections.map((section) => section.title),
    ['Now', 'Today', 'Worth Knowing'],
  );
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

test('gmail archive actions stay out of compact feed rows', () => {
  const cta = toSectionCta(
    createFeedItem({
      source: 'gmail',
      gmail_thread_id: 'thread-123',
      gmail_thread_action: 'archive',
    }),
  );

  assert.equal(cta, undefined);
});

test('YC RSVP items expose expandable detail instead of an inline CTA', () => {
  const item = createFeedItem({
    source: 'gmail',
    title: 'RSVP for YC Startup School India',
    primary_action: 'confirm',
    why_this_is_here: 'YC has accepted your application to attend Startup School India.',
  });

  assert.equal(toActionSentence(item), 'RSVP for YC Startup School India');
  assert.equal(toSectionCta(item), undefined);
  assert.deepEqual(toSectionDetail(item), {
    facts: [
      { label: 'Current', value: 'Waiting on you' },
      { label: 'Next', value: 'RSVP before the attendee list closes' },
    ],
    body: [
      'YC has accepted your application to attend Startup School India.',
      'The talk is in Bangalore. Only confirm if you can attend.',
      'YC will send a calendar invite after you RSVP.',
    ],
    evidence: ['YC acceptance email', 'Follow-up RSVP reminder', 'Event details from the same thread'],
    confirmLabel: 'Yes, I can attend',
    dismissLabel: 'No',
    sourceLabel: 'Sources: 3 emails from YC',
    links: {
      threadHref: '/entities/entity-1/thread',
    },
  });
});

test('demo inbox items expose natural detail, one action, and source link', () => {
  const detail = toSectionDetail(
    createFeedItem({
      id: 'northstar-card-bill',
      entity_id: 'entity-northstar-card-bill',
      source: 'gmail',
      title: 'Northstar credit card bill due today',
      primary_action: 'pay',
      why_this_is_here: 'The statement says autopay is off and the bill is due by 5 PM.',
    }),
  );

  assert.match(detail?.body.join(' ') ?? '', /latest Northstar statement says autopay/);
  assert.equal(detail?.actionLabel ?? detail?.confirmLabel, 'Paid');
  assert.deepEqual(detail?.evidence, [
    'Northstar statement email',
    'Payment reminder from alerts@northstarbank.example',
    'No matching payment receipt found today',
  ]);
  assert.deepEqual(detail?.links, {
    threadHref: '/entities/entity-northstar-card-bill/thread',
  });
});

test('generic Gmail detail hides internal Gmail thread and pipeline trace ids', () => {
  const detail = toSectionDetail(
    createFeedItem({
      id: 'hsbc-query',
      entity_id: 'entity-hsbc-query',
      source: 'gmail',
      gmail_thread_id: '19d82390812fb384',
      trace_id: '695df90d-4122-4ac5-94bc-6aa30d11f9dd',
      current_state: 'waiting',
      primary_action: 'open',
      title: 'HSBC is reviewing your savings account query after you corrected the account number.',
      why_this_is_here:
        'You corrected the account number, explained that the phone calls did not resolve your issue, and asked HSBC to follow up by email.',
    }),
  );

  assert.equal(detail?.body.length, 1);
  assert.match(detail?.body[0] ?? '', /asked HSBC to follow up by email/);
  assert.equal(detail?.actionLabel, 'Wait for the reply');
  assert.equal(detail?.sourceLabel, 'Gmail');
  assert.doesNotMatch(detail?.sourceLabel ?? '', /19d82390812fb384/);
  assert.doesNotMatch(JSON.stringify({ body: detail?.body, evidence: detail?.evidence }), /695df90d|19d823/);
  assert.deepEqual(detail?.links, {
    threadHref: '/entities/entity-hsbc-query/thread',
  });
});
