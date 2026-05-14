/** Unit tests for dashboard copy shaping and agenda extraction. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import type { FeedItem, FeedResponse } from '../../lib/types';
import { buildAgenda, buildSections, buildSummary, toActionSentence, toSectionCta, toSectionDetail } from './page';

const dashboardPageSource = readFileSync(new URL('./page.tsx', import.meta.url), 'utf8');
const dashboardClientSource = readFileSync(new URL('./DashboardClient.tsx', import.meta.url), 'utf8');

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

test('dashboard route mounts the client cache instead of server-fetching dashboard data', () => {
  assert.match(dashboardPageSource, /\bDashboardClient\b/);
  assert.doesNotMatch(dashboardPageSource, /\bgetDashboard\b/);
  assert.match(dashboardClientSource, /\bDASHBOARD_STORAGE_PREFIX\b/);
  assert.match(dashboardClientSource, /\bfetchAuthMe\b/);
});

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
    title: 'HDFC Bank declined a recurring card payment as non-compliant.',
    primary_action: 'review',
  });

  assert.equal(toActionSentence(item), 'HDFC Bank declined a recurring card payment as non-compliant.');
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
          title: 'HDFC credit card bill due today',
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
          title: 'Your Samsung order #12304086779 was delivered.',
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

test('dashboard keeps work buckets available when the inbox is empty', () => {
  const sections = buildSections(createFeed());

  assert.deepEqual(
    sections.map((section) => ({ id: section.id, title: section.title, itemCount: section.items.length })),
    [
      { id: 'now', title: 'Now', itemCount: 0 },
      { id: 'today', title: 'Today', itemCount: 0 },
      { id: 'worth-knowing', title: 'Worth Knowing', itemCount: 0 },
    ],
  );
});

test('dashboard summary renders backend-generated briefing copy without rewriting it', () => {
  const summary = buildSummary({
    briefing: {
      headline: 'Good morning, Jordan.',
      brief: 'You have 0 meetings and a mostly open afternoon.',
    },
  });

  assert.deepEqual(summary, {
    headline: 'Good morning, Jordan.',
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

test('confirmation items use generic expandable detail instead of hardcoded vendor copy', () => {
  const item = createFeedItem({
    source: 'gmail',
    title: 'RSVP for the founder event',
    primary_action: 'confirm',
    why_this_is_here: 'The organizer accepted your application and needs your RSVP.',
  });

  assert.equal(toActionSentence(item), 'RSVP for the founder event');
  assert.equal(toSectionCta(item), undefined);
  assert.deepEqual(toSectionDetail(item), {
    body: ['The organizer accepted your application and needs your RSVP.'],
    actionLabel: 'Confirm or decline',
    confirmLabel: 'Confirmed',
    dismissLabel: 'Not needed',
    sourceLabel: 'Gmail',
    links: {
      threadHref: '/entities/entity-1/thread',
    },
  });
});

test('gmail items without backend detail use generic fallback copy and source link', () => {
  const detail = toSectionDetail(
    createFeedItem({
      id: 'card-bill',
      entity_id: 'entity-card-bill',
      source: 'gmail',
      title: 'Credit card bill due today',
      primary_action: 'pay',
      why_this_is_here: 'The statement says autopay is off and the bill is due by 5 PM.',
    }),
  );

  assert.deepEqual(detail?.body, ['The statement says autopay is off and the bill is due by 5 PM.']);
  assert.equal(detail?.actionLabel, 'Make the payment');
  assert.equal(detail?.sourceLabel, 'Gmail');
  assert.deepEqual(detail?.links, {
    threadHref: '/entities/entity-card-bill/thread',
  });
});

test('backend-provided Gmail detail copy wins over frontend fallback shaping', () => {
  const detail = toSectionDetail(
    createFeedItem({
      entity_id: 'entity-clean-detail',
      source: 'gmail',
      current_state: 'waiting',
      primary_action: 'open',
      title: 'Short backend title',
      why_this_is_here: 'Old fallback copy.',
      detail: {
        body: ['HSBC is still reviewing your savings account query after you sent the corrected account number.'],
        action_label: 'Wait for the reply',
        source_label: 'Gmail',
      },
    }),
  );

  assert.deepEqual(detail, {
    body: ['HSBC is still reviewing your savings account query after you sent the corrected account number.'],
    actionLabel: 'Wait for the reply',
    confirmLabel: 'Read',
    dismissLabel: 'Not needed',
    sourceLabel: 'Gmail',
    links: {
      threadHref: '/entities/entity-clean-detail/thread',
    },
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
