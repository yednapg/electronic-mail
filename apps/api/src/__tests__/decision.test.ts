import assert from 'node:assert/strict';
import test, { describe } from 'node:test';

import { assertDecisionLikeTitle, assertSingleAction } from './helpers';

type DecisionOutput = {
  id: string;
  is_decision: boolean;
  title: string;
  why_this_is_here: string;
  primary_action: string;
  timing_band: 'now' | 'today' | 'later' | 'hidden';
  importance_level: 'high' | 'medium' | 'low';
  action_confidence: 'high' | 'medium' | 'low';
};

const VALID_ACTIONS = new Set([
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
]);

describe('decision output validation', () => {
  test('valid decision outputs pass structural and intent checks', () => {
    const mockedPythonOutput: DecisionOutput[] = [
      {
        id: '1',
        is_decision: true,
        title: 'Reply to Priya about design review notes',
        why_this_is_here: 'Priya is waiting on your response before the review.',
        primary_action: 'reply',
        timing_band: 'now',
        importance_level: 'high',
        action_confidence: 'high',
      },
      {
        id: '2',
        is_decision: true,
        title: 'Review the vendor security questionnaire',
        why_this_is_here: 'Legal needs your review before sending it back tonight.',
        primary_action: 'review',
        timing_band: 'today',
        importance_level: 'medium',
        action_confidence: 'medium',
      },
      {
        id: '3',
        is_decision: false,
        title: '',
        why_this_is_here: '',
        primary_action: '',
        timing_band: 'hidden',
        importance_level: 'low',
        action_confidence: 'low',
      },
    ];

    const visibleItems = mockedPythonOutput.filter((item) => item.is_decision);

    for (const item of visibleItems) {
      validateDecisionOutput(item);
    }

    assert.equal(visibleItems.length, 2);
  });

  test('rejects vague titles', () => {
    const item: DecisionOutput = {
      id: 'bad-vague',
      is_decision: true,
      title: 'Check this',
      why_this_is_here: 'Something might need attention.',
      primary_action: 'review',
      timing_band: 'today',
      importance_level: 'medium',
      action_confidence: 'low',
    };

    assert.throws(() => validateDecisionOutput(item), /too vague/);
  });

  test('rejects missing action', () => {
    const item: DecisionOutput = {
      id: 'bad-action',
      is_decision: true,
      title: 'Reply to finance about the invoice',
      why_this_is_here: 'Finance needs your response today.',
      primary_action: '',
      timing_band: 'today',
      importance_level: 'high',
      action_confidence: 'high',
    };

    assert.throws(() => validateDecisionOutput(item), /primary_action must be non-empty/);
  });

  test('rejects multiple actions in one title', () => {
    const item: DecisionOutput = {
      id: 'bad-multiple',
      is_decision: true,
      title: 'Reply and confirm the meeting',
      why_this_is_here: 'Two actions are being mixed together.',
      primary_action: 'reply',
      timing_band: 'today',
      importance_level: 'medium',
      action_confidence: 'medium',
    };

    assert.throws(() => validateDecisionOutput(item), /must not imply multiple actions/);
  });
});

function validateDecisionOutput(item: DecisionOutput): void {
  assert.equal(item.is_decision, true, 'visible decision outputs must have is_decision=true');
  assertDecisionLikeTitle(item.title);
  assertSingleAction(item.primary_action);
  assert.ok(VALID_ACTIONS.has(item.primary_action), `unsupported action ${item.primary_action}`);
  assert.ok(item.why_this_is_here.trim().length > 0, 'why_this_is_here must be present');
  assert.ok(
    ['now', 'today', 'later'].includes(item.timing_band),
    `invalid timing band ${item.timing_band}`,
  );
  assert.ok(
    ['high', 'medium', 'low'].includes(item.importance_level),
    `invalid importance level ${item.importance_level}`,
  );
  assert.ok(
    ['high', 'medium', 'low'].includes(item.action_confidence),
    `invalid action confidence ${item.action_confidence}`,
  );
}

