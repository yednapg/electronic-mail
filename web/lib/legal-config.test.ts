import assert from 'node:assert/strict';
import test from 'node:test';

import { legalConfigIssues, loadLegalConfig } from './legal-config';

test('legal launch configuration rejects placeholders and unreviewed copy', () => {
  const config = loadLegalConfig({
    LEGAL_ENTITY_NAME: 'OWNER REVIEW REQUIRED',
    LEGAL_SUPPORT_EMAIL: 'support@example.com',
    STATUS_PAGE_URL: 'http://localhost:5173/status',
    LEGAL_EFFECTIVE_DATE: 'later',
    LEGAL_JURISDICTION: 'OWNER REVIEW REQUIRED',
    LEGAL_HOSTING_PROVIDERS: 'OWNER REVIEW REQUIRED',
    LEGAL_DATA_REGIONS: 'OWNER REVIEW REQUIRED',
    LEGAL_BACKUP_RETENTION_DAYS: '0',
    LEGAL_REVIEW_STATUS: 'needs-review',
  });

  assert.deepEqual(legalConfigIssues(config), [
    'legal entity',
    'support email',
    'status page URL',
    'effective date',
    'jurisdiction',
    'hosting providers',
    'data regions',
    'backup retention',
    'legal review approval',
  ]);
});

test('legal launch configuration accepts owner-approved production values', () => {
  const config = loadLegalConfig({
    LEGAL_ENTITY_NAME: 'Electronic Mail Private Limited',
    LEGAL_SUPPORT_EMAIL: 'support@electronicmail.app',
    STATUS_PAGE_URL: 'https://status.electronicmail.app',
    LEGAL_EFFECTIVE_DATE: '2026-07-13',
    LEGAL_JURISDICTION: 'Karnataka, India',
    LEGAL_HOSTING_PROVIDERS: 'Railway and its contracted cloud infrastructure providers',
    LEGAL_DATA_REGIONS: 'the publisher-approved production region',
    LEGAL_BACKUP_RETENTION_DAYS: '30',
    LEGAL_REVIEW_STATUS: 'approved',
  });

  assert.deepEqual(legalConfigIssues(config), []);
});

test('legal launch configuration rejects reserved and non-monitored contact addresses', () => {
  for (const supportEmail of [
    'support@example.com',
    'support@service.test',
    'support@service.invalid',
    'test@electronicmail.app',
    'no-reply@electronicmail.app',
    'support@localhost',
  ]) {
    const config = loadLegalConfig({
      LEGAL_ENTITY_NAME: 'Electronic Mail Private Limited',
      LEGAL_SUPPORT_EMAIL: supportEmail,
      STATUS_PAGE_URL: 'https://status.electronicmail.app',
      LEGAL_EFFECTIVE_DATE: '2026-07-13',
      LEGAL_JURISDICTION: 'Karnataka, India',
      LEGAL_HOSTING_PROVIDERS: 'Railway and its contracted cloud infrastructure providers',
      LEGAL_DATA_REGIONS: 'the publisher-approved production region',
      LEGAL_BACKUP_RETENTION_DAYS: '30',
      LEGAL_REVIEW_STATUS: 'approved',
    });

    assert.ok(legalConfigIssues(config).includes('support email'), supportEmail);
  }
});

test('legal launch configuration requires a public HTTPS status page', () => {
  for (const statusPageURL of [
    '',
    'http://status.electronicmail.app',
    'https://status.example.com',
    'https://status.service.test',
    'https://localhost/status',
    'https://127.0.0.1/status',
    'not-a-url',
  ]) {
    const config = loadLegalConfig({
      LEGAL_ENTITY_NAME: 'Electronic Mail Private Limited',
      LEGAL_SUPPORT_EMAIL: 'support@electronicmail.app',
      STATUS_PAGE_URL: statusPageURL,
      LEGAL_EFFECTIVE_DATE: '2026-07-13',
      LEGAL_JURISDICTION: 'Karnataka, India',
      LEGAL_HOSTING_PROVIDERS: 'Railway and its contracted cloud infrastructure providers',
      LEGAL_DATA_REGIONS: 'the publisher-approved production region',
      LEGAL_BACKUP_RETENTION_DAYS: '30',
      LEGAL_REVIEW_STATUS: 'approved',
    });

    assert.ok(legalConfigIssues(config).includes('status page URL'), statusPageURL || '(empty)');
  }
});

test('legal launch configuration requires an exact bounded retention integer', () => {
  for (const retentionDays of ['', '0', '014', '30days', '30.5', '1e2', '366', '9007199254740993']) {
    const config = loadLegalConfig({
      LEGAL_ENTITY_NAME: 'Electronic Mail Private Limited',
      LEGAL_SUPPORT_EMAIL: 'support@electronicmail.app',
      STATUS_PAGE_URL: 'https://status.electronicmail.app',
      LEGAL_EFFECTIVE_DATE: '2026-07-13',
      LEGAL_JURISDICTION: 'Karnataka, India',
      LEGAL_HOSTING_PROVIDERS: 'Railway and its contracted cloud infrastructure providers',
      LEGAL_DATA_REGIONS: 'the publisher-approved production region',
      LEGAL_BACKUP_RETENTION_DAYS: retentionDays,
      LEGAL_REVIEW_STATUS: 'approved',
    });

    assert.ok(legalConfigIssues(config).includes('backup retention'), retentionDays || '(empty)');
  }
});

test('legal launch configuration validates real Gregorian calendar dates', () => {
  for (const effectiveDate of ['0000-01-01', '2026-00-10', '2026-13-10', '2026-04-31', '2026-02-29', '2024-02-30']) {
    const config = loadLegalConfig({
      LEGAL_ENTITY_NAME: 'Electronic Mail Private Limited',
      LEGAL_SUPPORT_EMAIL: 'support@electronicmail.app',
      STATUS_PAGE_URL: 'https://status.electronicmail.app',
      LEGAL_EFFECTIVE_DATE: effectiveDate,
      LEGAL_JURISDICTION: 'Karnataka, India',
      LEGAL_HOSTING_PROVIDERS: 'Railway and its contracted cloud infrastructure providers',
      LEGAL_DATA_REGIONS: 'the publisher-approved production region',
      LEGAL_BACKUP_RETENTION_DAYS: '30',
      LEGAL_REVIEW_STATUS: 'approved',
    });

    assert.ok(legalConfigIssues(config).includes('effective date'), effectiveDate);
  }

  const leapDay = loadLegalConfig({
    LEGAL_ENTITY_NAME: 'Electronic Mail Private Limited',
    LEGAL_SUPPORT_EMAIL: 'support@electronicmail.app',
    STATUS_PAGE_URL: 'https://status.electronicmail.app',
    LEGAL_EFFECTIVE_DATE: '2024-02-29',
    LEGAL_JURISDICTION: 'Karnataka, India',
    LEGAL_HOSTING_PROVIDERS: 'Railway and its contracted cloud infrastructure providers',
    LEGAL_DATA_REGIONS: 'the publisher-approved production region',
    LEGAL_BACKUP_RETENTION_DAYS: '30',
    LEGAL_REVIEW_STATUS: 'approved',
  });
  assert.equal(legalConfigIssues(leapDay).includes('effective date'), false);
});

test('legal launch configuration requires reviewed hosting and region disclosures', () => {
  for (const [field, value, issue] of [
    ['LEGAL_HOSTING_PROVIDERS', 'TBD', 'hosting providers'],
    ['LEGAL_DATA_REGIONS', 'unknown', 'data regions'],
  ] as const) {
    const environment = {
      LEGAL_ENTITY_NAME: 'Electronic Mail Private Limited',
      LEGAL_SUPPORT_EMAIL: 'support@electronicmail.app',
      STATUS_PAGE_URL: 'https://status.electronicmail.app',
      LEGAL_EFFECTIVE_DATE: '2026-07-13',
      LEGAL_JURISDICTION: 'Karnataka, India',
      LEGAL_HOSTING_PROVIDERS: 'Railway and its contracted cloud infrastructure providers',
      LEGAL_DATA_REGIONS: 'the publisher-approved production region',
      LEGAL_BACKUP_RETENTION_DAYS: '30',
      LEGAL_REVIEW_STATUS: 'approved',
      [field]: value,
    };

    assert.ok(legalConfigIssues(loadLegalConfig(environment)).includes(issue));
  }
});
