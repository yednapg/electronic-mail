import assert from 'node:assert/strict';
import test, { describe } from 'node:test';

import type { FeedResponse } from '@decision-pipeline/types';

import { validateFeed } from './helpers';

const morningFeed = require('../../../web/dev-data/feed-morning.json') as FeedResponse;
const afternoonFeed = require('../../../web/dev-data/feed-afternoon.json') as FeedResponse;
const nightFeed = require('../../../web/dev-data/feed-night.json') as FeedResponse;
const chaosFeed = require('../../../web/dev-data/feed-chaos.json') as FeedResponse;

describe('feed behavior', () => {
  test('morning, afternoon, night, and chaos feeds remain valid and deduplicated', () => {
    for (const feed of [morningFeed, afternoonFeed, nightFeed, chaosFeed]) {
      validateFeed(feed);

      const total = countVisible(feed);
      assert.ok(total > 0, 'feed must not be empty');
      assert.ok(total < 20, 'feed must not explode into an extreme number of visible items');
    }
  });

  test('urgent items appear in now when present', () => {
    assert.ok(morningFeed.now.length > 0, 'morning feed should have urgent items');
    assert.ok(afternoonFeed.now.length > 0, 'afternoon feed should have urgent items');
    assert.ok(nightFeed.now.length > 0, 'night feed should have urgent items');
    assert.ok(chaosFeed.now.length > 0, 'chaos feed should have urgent items');
  });

  test('later section does not dominate the main feed', () => {
    for (const feed of [morningFeed, afternoonFeed, nightFeed, chaosFeed]) {
      const mainCount = feed.now.length + feed.today.length;
      const laterCount = feed.worth_knowing.length;

      assert.ok(
        laterCount <= mainCount + 2,
        'later section should not overwhelm the main decision surface',
      );
    }
  });
});

describe('time evolution', () => {
  test('items move forward across morning, afternoon, and night', () => {
    assertSection(morningFeed, 'submit-pycon-proposal', 'today');
    assertSection(afternoonFeed, 'submit-pycon-proposal', 'now');
    assertMissing(nightFeed, 'submit-pycon-proposal');

    assertSection(morningFeed, 'pay-card-bill', 'today');
    assertSection(afternoonFeed, 'pay-card-bill', 'today');
    assertSection(nightFeed, 'pay-card-bill', 'now');

    assertSection(morningFeed, 'confirm-startup-school', 'worth_knowing');
    assertSection(afternoonFeed, 'confirm-startup-school', 'worth_knowing');
    assertSection(nightFeed, 'confirm-startup-school', 'worth_knowing');
  });

  test('shared item ordering remains stable when section membership is unchanged', () => {
    assert.deepEqual(
      afternoonFeed.today.map((item) => item.id),
      ['pay-card-bill', 'review-security-questionnaire'],
    );
    assert.deepEqual(
      nightFeed.now.map((item) => item.id),
      ['pay-card-bill', 'review-security-questionnaire'],
    );
  });
});

describe('chaos dataset', () => {
  test('chaos feed remains actionable and does not flatten all priority equally', () => {
    validateFeed(chaosFeed);

    const allItems = [...chaosFeed.now, ...chaosFeed.today, ...chaosFeed.worth_knowing];
    const uniqueTimingBands = new Set(allItems.map((item) => item.timing_band));

    assert.ok(chaosFeed.now.length > 0, 'chaos feed should still surface urgent work');
    assert.ok(chaosFeed.today.length > 0, 'chaos feed should still surface today work');
    assert.ok(uniqueTimingBands.size >= 2, 'chaos feed should preserve prioritization');

    for (const item of allItems) {
      assert.ok(item.need_type === 'decision', `non-decision item leaked into feed: ${item.id}`);
      assert.ok(
        !/processed successfully|weekly roundup|newsletter/i.test(item.title + item.why_this_is_here),
        `informational noise leaked into feed: ${item.id}`,
      );
    }
  });
});

function countVisible(feed: FeedResponse): number {
  return feed.now.length + feed.today.length + feed.worth_knowing.length;
}

function assertSection(
  feed: FeedResponse,
  itemId: string,
  section: keyof FeedResponse,
): void {
  const ids = feed[section].map((item) => item.id);

  assert.ok(ids.includes(itemId), `${itemId} should appear in ${section}`);
}

function assertMissing(feed: FeedResponse, itemId: string): void {
  const ids = [...feed.now, ...feed.today, ...feed.worth_knowing].map((item) => item.id);

  assert.ok(!ids.includes(itemId), `${itemId} should no longer be visible`);
}

