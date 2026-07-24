import assert from 'node:assert/strict';
import test from 'node:test';

import {
  isProductionWebRuntime,
  isWebProductPath,
  isWebProductUIEnabled,
  shouldBlockProductionWebPath,
} from './web-product-boundary';

test('production always disables the legacy web product UI', () => {
  assert.equal(isProductionWebRuntime({ NODE_ENV: 'production' }), true);
  assert.equal(isProductionWebRuntime({ NODE_ENV: 'development', APP_ENV: 'production' }), true);
  assert.equal(isWebProductUIEnabled({ NODE_ENV: 'production' }), false);
  assert.equal(isWebProductUIEnabled({ NODE_ENV: 'development' }), true);
});

test('the boundary covers every legacy product page and web BFF endpoint', () => {
  for (const pathname of [
    '/gmail',
    '/gmail/threads/thread-1',
    '/dashboard',
    '/dashboard/detail',
    '/api',
    '/api/mailbox',
    '/api/dashboard',
  ]) {
    assert.equal(isWebProductPath(pathname), true, pathname);
    assert.equal(shouldBlockProductionWebPath(pathname, { NODE_ENV: 'production' }), true, pathname);
    assert.equal(shouldBlockProductionWebPath(pathname, { NODE_ENV: 'development' }), false, pathname);
  }
});

test('the production boundary preserves public and OAuth-completion pages', () => {
  for (const pathname of ['/', '/healthz', '/post-login', '/privacy', '/terms', '/support', '/icon.svg']) {
    assert.equal(isWebProductPath(pathname), false, pathname);
    assert.equal(shouldBlockProductionWebPath(pathname, { APP_ENV: 'production' }), false, pathname);
  }
});

test('route-prefix checks do not block similarly named public paths', () => {
  assert.equal(isWebProductPath('/gmail-help'), false);
  assert.equal(isWebProductPath('/dashboard-status'), false);
});
