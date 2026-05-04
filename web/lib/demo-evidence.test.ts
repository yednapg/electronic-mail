import test from 'node:test';
import assert from 'node:assert/strict';

import { getDemoSourceRecords, getDemoTrace } from './demo-evidence';

test('demo evidence returns item-scoped raw Gmail records', () => {
  const records = getDemoSourceRecords('northstar-card-bill');

  assert.equal(records.length, 2);
  assert.equal(records[0].thread_id, 'northstar-card-statement-feb');
  assert.equal(records[0].raw_payload.detected_item_id, 'northstar-card-bill');
  assert.match(String(records[0].raw_payload.subject), /Northstar credit card/);
});

test('demo trace replays the frontend evidence pipeline for one entity', () => {
  const trace = getDemoTrace('entity-northstar-card-bill');

  assert.equal(trace?.entity_id, 'entity-northstar-card-bill');
  assert.deepEqual(
    trace?.items.map((item) => item.stage),
    ['ingestion', 'grouping', 'state_derivation', 'decision', 'action_selection', 'output'],
  );
  assert.equal(trace?.items[2].output.current_state, 'open');
});
