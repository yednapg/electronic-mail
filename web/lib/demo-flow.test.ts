import test from 'node:test';
import assert from 'node:assert/strict';

import {
  DEMO_DASHBOARD_ROUTE,
  DEMO_SIGN_IN_ROUTE,
  POST_LOGIN_MINIMUM_MS,
  POST_LOGIN_READY_TIMEOUT_MS,
} from './demo-flow';

test('demo sign-in flow routes through the animated post-login screen', () => {
  assert.equal(DEMO_SIGN_IN_ROUTE, '/post-login');
  assert.equal(DEMO_DASHBOARD_ROUTE, '/dashboard');
  assert.ok(POST_LOGIN_MINIMUM_MS >= 7000);
  assert.ok(POST_LOGIN_MINIMUM_MS <= 9000);
  assert.ok(POST_LOGIN_READY_TIMEOUT_MS >= POST_LOGIN_MINIMUM_MS);
});
