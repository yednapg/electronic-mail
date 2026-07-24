import assert from 'node:assert/strict';
import test from 'node:test';

import { createHealthResponse } from './route';

function productionEnvironment(
  overrides: Record<string, string | undefined> = {},
): Record<string, string | undefined> {
  return {
    NODE_ENV: 'production',
    ELECTRONIC_MAIL_BACKEND_URL: 'https://api.electronicmail.app',
    MACOS_DOWNLOAD_ENABLED: 'true',
    MACOS_DOWNLOAD_URL: 'https://downloads.electronicmail.app/ElectronicMail.dmg',
    LEGAL_ENTITY_NAME: 'Electronic Mail Incorporated',
    STATUS_PAGE_URL: 'https://status.electronicmail.app',
    LEGAL_EFFECTIVE_DATE: '2026-07-24',
    LEGAL_JURISDICTION: 'Karnataka, India',
    LEGAL_HOSTING_PROVIDERS: 'Reviewed infrastructure providers',
    LEGAL_DATA_REGIONS: 'Reviewed production data regions',
    LEGAL_BACKUP_RETENTION_DAYS: '14',
    LEGAL_REVIEW_STATUS: 'approved',
    ...overrides,
  };
}

test('/healthz returns ready only for complete public launch configuration', async () => {
  const response = createHealthResponse(productionEnvironment());

  assert.equal(response.status, 200);
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
  assert.deepEqual(await response.json(), {
    status: 'ready',
    checks: {
      backend: true,
      download: true,
      legal: true,
    },
  });
});

test('/healthz returns degraded for an explicit download pause', async () => {
  const response = createHealthResponse(productionEnvironment({
    MACOS_DOWNLOAD_ENABLED: 'false',
    MACOS_DOWNLOAD_URL: undefined,
  }));

  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    status: 'degraded',
    checks: {
      backend: true,
      download: false,
      legal: true,
    },
  });
});

test('/healthz returns not ready for absent, unknown, or broken download configuration', async () => {
  for (const [label, override] of [
    ['absent flag', { MACOS_DOWNLOAD_ENABLED: undefined }],
    ['unknown flag', { MACOS_DOWNLOAD_ENABLED: 'sometimes' }],
    ['absent URL', { MACOS_DOWNLOAD_URL: undefined }],
    ['invalid URL', { MACOS_DOWNLOAD_URL: 'https://downloads.example.com/ElectronicMail.dmg' }],
  ] as const) {
    const response = createHealthResponse(productionEnvironment(override));

    assert.equal(response.status, 503, label);
    assert.deepEqual(await response.json(), {
      status: 'not_ready',
      checks: {
        backend: true,
        download: false,
        legal: true,
      },
    }, label);
  }
});
