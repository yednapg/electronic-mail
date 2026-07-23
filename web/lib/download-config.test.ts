import assert from 'node:assert/strict';
import test from 'node:test';

import { downloadConfigIssues, loadDownloadConfig } from './download-config';

test('download launch configuration accepts an enabled public HTTPS artifact', () => {
  const config = loadDownloadConfig({
    MACOS_DOWNLOAD_ENABLED: 'true',
    MACOS_DOWNLOAD_URL: 'https://downloads.electronicmail.app/ElectronicMail.dmg',
  });

  assert.deepEqual(downloadConfigIssues(config), []);
});

test('download launch configuration rejects disabled, local, and placeholder artifacts', () => {
  assert.deepEqual(downloadConfigIssues(loadDownloadConfig({})), [
    'downloads disabled',
    'macOS download URL',
  ]);

  for (const url of [
    'http://downloads.electronicmail.app/ElectronicMail.dmg',
    'https://downloads.example.com/ElectronicMail.dmg',
    'https://localhost/ElectronicMail.dmg',
    'not-a-url',
  ]) {
    const config = loadDownloadConfig({
      MACOS_DOWNLOAD_ENABLED: 'true',
      MACOS_DOWNLOAD_URL: url,
    });
    assert.ok(downloadConfigIssues(config).includes('macOS download URL'), url);
  }
});
