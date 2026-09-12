import assert from 'node:assert/strict';
import test from 'node:test';

import { evaluatePublicLaunchReadiness } from './public-launch-readiness';

function productionEnvironment(
  overrides: Record<string, string | undefined> = {},
): Record<string, string | undefined> {
  return {
    NODE_ENV: 'production',
    ELECTRONIC_MAIL_BACKEND_URL: 'https://api.electronicmail.app',
    MACOS_DOWNLOAD_ENABLED: 'true',
    MACOS_DOWNLOAD_URL: 'https://downloads.electronicmail.app/ElectronicMail.dmg',
    ...overrides,
  };
}

test('public launch readiness passes only complete approved production configuration', () => {
  assert.deepEqual(evaluatePublicLaunchReadiness(productionEnvironment()), {
    serving: true,
    ready: true,
    checks: {
      backend: true,
      download: true,
    },
  });
});

test('public launch readiness remains available during an intentional download pause', () => {
  const result = evaluatePublicLaunchReadiness(productionEnvironment({
    MACOS_DOWNLOAD_ENABLED: 'false',
    MACOS_DOWNLOAD_URL: undefined,
  }));

  assert.equal(result.serving, true);
  assert.equal(result.ready, false);
  assert.equal(result.checks.download, false);
});

test('public launch readiness fails closed for missing or unknown download enablement', () => {
  for (const [label, value] of [
    ['missing', undefined],
    ['unknown', 'sometimes'],
  ] as const) {
    const result = evaluatePublicLaunchReadiness(productionEnvironment({
      MACOS_DOWNLOAD_ENABLED: value,
    }));
    assert.equal(result.serving, false, label);
    assert.equal(result.ready, false, label);
    assert.equal(result.checks.download, false, label);
  }
});

test('public launch readiness fails closed when enabled download URL is missing or invalid', () => {
  for (const [label, value] of [
    ['missing', undefined],
    ['invalid', 'https://downloads.example.com/ElectronicMail.dmg'],
  ] as const) {
    const result = evaluatePublicLaunchReadiness(productionEnvironment({
      MACOS_DOWNLOAD_URL: value,
    }));
    assert.equal(result.serving, false, label);
    assert.equal(result.ready, false, label);
    assert.equal(result.checks.download, false, label);
  }
});

test('public launch readiness fails serving health for backend misconfiguration', () => {
  const result = evaluatePublicLaunchReadiness(productionEnvironment({
    ELECTRONIC_MAIL_BACKEND_URL: 'http://localhost:3001',
  }));
  assert.equal(result.serving, false);
  assert.equal(result.ready, false);
});
