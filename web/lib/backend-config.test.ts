import assert from 'node:assert/strict';
import test from 'node:test';

import { resolveBackendURL } from './backend-config';

test('production backend configuration accepts one public HTTPS origin', () => {
  assert.equal(resolveBackendURL({
    NODE_ENV: 'production',
    DECISION_PIPELINE_BACKEND_URL: 'https://api.electronicmail.app/',
  }), 'https://api.electronicmail.app');
});

test('production backend configuration rejects SSRF-prone and placeholder origins', () => {
  for (const value of [
    '',
    'http://api.electronicmail.app',
    'https://api.example.com',
    'https://backend.service.test',
    'https://localhost:3001',
    'https://127.0.0.1',
    'https://169.254.169.254',
    'https://user:password@api.electronicmail.app',
    'https://api.electronicmail.app/v1',
    'https://api.electronicmail.app?target=other',
  ]) {
    assert.throws(
      () => resolveBackendURL({
        NODE_ENV: 'production',
        DECISION_PIPELINE_BACKEND_URL: value,
      }),
      { name: 'Error' },
      value || '(empty)',
    );
  }
});

test('local development keeps the explicit localhost backend', () => {
  assert.equal(resolveBackendURL({
    NODE_ENV: 'development',
    DECISION_PIPELINE_BACKEND_URL: 'http://localhost:3001/',
  }), 'http://localhost:3001');
});
