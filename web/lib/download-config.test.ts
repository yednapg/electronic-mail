import assert from 'node:assert/strict';
import test from 'node:test';

import { downloadConfigIssues, loadDownloadConfig } from './download-config';

test('download launch configuration accepts an enabled public HTTPS artifact', () => {
  const config = loadDownloadConfig({
    MACOS_DOWNLOAD_ENABLED: 'true',
    MACOS_DOWNLOAD_URL: 'https://downloads.electronicmail.app/ElectronicMail.dmg',
  });

  assert.equal(config.enablement, 'enabled');
  assert.deepEqual(downloadConfigIssues(config), []);
});

test('download launch configuration distinguishes an explicit pause from a missing flag', () => {
  assert.deepEqual(loadDownloadConfig({
    MACOS_DOWNLOAD_ENABLED: ' FALSE ',
  }), {
    enablement: 'paused',
    url: '',
  });
  assert.deepEqual(downloadConfigIssues(loadDownloadConfig({
    MACOS_DOWNLOAD_ENABLED: 'false',
  })), ['downloads disabled']);

  assert.deepEqual(downloadConfigIssues(loadDownloadConfig({})), [
    'macOS download enablement',
    'macOS download URL',
  ]);
  assert.deepEqual(downloadConfigIssues(loadDownloadConfig({
    MACOS_DOWNLOAD_ENABLED: 'sometimes',
    MACOS_DOWNLOAD_URL: 'https://downloads.electronicmail.app/ElectronicMail.dmg',
  })), ['macOS download enablement']);
});

test('download launch configuration rejects local and placeholder artifacts', () => {

  for (const url of [
    'http://downloads.electronicmail.app/ElectronicMail.dmg',
    'https://downloads.example.com/ElectronicMail.dmg',
    'https://localhost/ElectronicMail.dmg',
    'https://downloads.electronicmail.app:443/ElectronicMail.dmg',
    'https://downloads.electronicmail.app/ElectronicMail.zip',
    'https://downloads.electronicmail.app/ElectronicMail.dmg?token=public',
    'https://downloads.electronicmail.app/ElectronicMail.dmg#latest',
    'https://downloads.electronicmail.app/releases/../ElectronicMail.dmg',
    'https://DOWNLOADS.electronicmail.app/ElectronicMail.dmg',
    'https://downloads.electronicmail.app./ElectronicMail.dmg',
    'https://user@downloads.electronicmail.app/ElectronicMail.dmg',
    'not-a-url',
  ]) {
    const config = loadDownloadConfig({
      MACOS_DOWNLOAD_ENABLED: 'true',
      MACOS_DOWNLOAD_URL: url,
    });
    assert.ok(downloadConfigIssues(config).includes('macOS download URL'), url);
  }
});
