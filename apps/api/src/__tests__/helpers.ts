import assert from 'node:assert/strict';

import type { FeedResponse } from '@electronic-mail/types';

const VALID_TIMING_BANDS = new Set(['now', 'today', 'later']);
const VAGUE_TITLES = [/^check this$/i, /^see update$/i, /^view details$/i];
const TITLE_ACTION_VERBS = [
  'reply',
  'confirm',
  'pay',
  'join',
  'review',
  'send',
  'approve',
  'open',
  'register',
  'track',
  'submit',
];

export function validateFeed(feed: FeedResponse): void {
  assert.ok(Array.isArray(feed.now), 'feed.now must be an array');
  assert.ok(Array.isArray(feed.today), 'feed.today must be an array');
  assert.ok(Array.isArray(feed.worth_knowing), 'feed.worth_knowing must be an array');

  const allItems = [...feed.now, ...feed.today, ...feed.worth_knowing];
  const entityIds = new Set<string>();

  for (const item of allItems) {
    assert.ok(item.id.trim().length > 0, 'item.id must be non-empty');
    assert.ok(item.entity_id.trim().length > 0, 'item.entity_id must be non-empty');
    assert.ok(item.title.trim().length > 0, 'item.title must be non-empty');
    assert.ok(item.why_this_is_here.trim().length > 0, 'item.why_this_is_here must be non-empty');
    assert.ok(item.primary_action.trim().length > 0, 'item.primary_action must be non-empty');
    assert.ok(
      VALID_TIMING_BANDS.has(item.timing_band),
      `item ${item.id} must have a visible timing band`,
    );
    assert.ok(!entityIds.has(item.entity_id), `duplicate entity_id ${item.entity_id} in feed`);
    assert.ok(
      !item.title.includes('undefined') && !item.title.includes('null'),
      `item ${item.id} title must not contain undefined/null`,
    );
    assert.ok(
      !item.why_this_is_here.includes('undefined') && !item.why_this_is_here.includes('null'),
      `item ${item.id} why_this_is_here must not contain undefined/null`,
    );
    assertSingleAction(item.primary_action);

    entityIds.add(item.entity_id);
  }
}

export function assertDecisionLikeTitle(title: string): void {
  assert.ok(title.trim().length > 0, 'title must be non-empty');
  assert.ok(
    !VAGUE_TITLES.some((pattern) => pattern.test(title.trim())),
    `title "${title}" is too vague`,
  );

  const normalized = title.trim().toLowerCase();

  assert.ok(
    TITLE_ACTION_VERBS.some((verb) => normalized.startsWith(`${verb} `) || normalized === verb),
    `title "${title}" must start with an action verb`,
  );
  assert.ok(
    !/\b(and|\/)\b/.test(normalized),
    `title "${title}" must not imply multiple actions`,
  );
}

export function assertSingleAction(action: string): void {
  assert.ok(action.trim().length > 0, 'primary_action must be non-empty');
  assert.ok(!/\s+/.test(action.trim()), `primary_action "${action}" must be a single action token`);
}

