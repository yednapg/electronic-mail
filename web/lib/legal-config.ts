export type LegalConfig = {
  entityName: string;
  supportEmail: string;
  statusPageURL: string;
  effectiveDate: string;
  jurisdiction: string;
  hostingProviders: string;
  dataRegions: string;
  backupRetentionDays: number;
  reviewStatus: string;
};

const RESERVED_HOSTS = new Set([
  'example.com',
  'example.net',
  'example.org',
  'localhost',
  'localhost.localdomain',
]);
const RESERVED_HOST_SUFFIXES = [
  '.example',
  '.example.com',
  '.example.net',
  '.example.org',
  '.invalid',
  '.localhost',
  '.local',
  '.test',
];
const RESERVED_CONTACT_MAILBOX = /^(?:demo|example|fake|no-?reply|do-?not-?reply|placeholder|sample|test|testing)$/i;

export function loadLegalConfig(environment: Record<string, string | undefined> = process.env): LegalConfig {
  return {
    entityName: environment.LEGAL_ENTITY_NAME?.trim() || 'OWNER REVIEW REQUIRED',
    supportEmail: environment.LEGAL_SUPPORT_EMAIL?.trim() || 'support@example.com',
    statusPageURL: environment.STATUS_PAGE_URL?.trim() || 'https://status.example.com',
    effectiveDate: environment.LEGAL_EFFECTIVE_DATE?.trim() || 'OWNER REVIEW REQUIRED',
    jurisdiction: environment.LEGAL_JURISDICTION?.trim() || 'OWNER REVIEW REQUIRED',
    hostingProviders: environment.LEGAL_HOSTING_PROVIDERS?.trim() || 'OWNER REVIEW REQUIRED',
    dataRegions: environment.LEGAL_DATA_REGIONS?.trim() || 'OWNER REVIEW REQUIRED',
    backupRetentionDays: parseExactInteger(environment.LEGAL_BACKUP_RETENTION_DAYS),
    reviewStatus: environment.LEGAL_REVIEW_STATUS?.trim().toLowerCase() || 'needs-review',
  };
}

export function legalConfigIssues(config: LegalConfig): string[] {
  const issues: string[] = [];
  if (!config.entityName || /owner review|required|example|replace/i.test(config.entityName)) issues.push('legal entity');
  if (!isPublicSupportEmail(config.supportEmail)) {
    issues.push('support email');
  }
  if (!isPublicHTTPSURL(config.statusPageURL)) issues.push('status page URL');
  if (!isCalendarDate(config.effectiveDate)) issues.push('effective date');
  if (!config.jurisdiction || /owner review|required|example|replace/i.test(config.jurisdiction)) issues.push('jurisdiction');
  if (!isReviewedLegalFact(config.hostingProviders)) issues.push('hosting providers');
  if (!isReviewedLegalFact(config.dataRegions)) issues.push('data regions');
  if (!Number.isInteger(config.backupRetentionDays) || config.backupRetentionDays < 1 || config.backupRetentionDays > 365) {
    issues.push('backup retention');
  }
  if (config.reviewStatus !== 'approved') issues.push('legal review approval');
  return issues;
}

function isReviewedLegalFact(value: string): boolean {
  return value.length >= 2 && !/owner review|required|example|replace|tbd|todo|unknown/i.test(value);
}

function parseExactInteger(value: string | undefined): number {
  const normalized = value?.trim() || '';
  if (!/^(?:0|[1-9]\d*)$/.test(normalized)) return Number.NaN;
  const parsed = Number(normalized);
  return Number.isSafeInteger(parsed) ? parsed : Number.NaN;
}

function isCalendarDate(value: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return false;

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (year < 1 || month < 1 || month > 12 || day < 1) return false;

  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day <= daysInMonth[month - 1];
}

function isPublicSupportEmail(value: string): boolean {
  const match = /^([^@\s]+)@([^@\s]+)$/.exec(value.trim());
  if (!match) return false;

  const [, localPart, hostname] = match;
  if (
    localPart.startsWith('.')
    || localPart.endsWith('.')
    || localPart.includes('..')
    || RESERVED_CONTACT_MAILBOX.test(localPart.split('+', 1)[0])
  ) {
    return false;
  }

  return isPlausiblePublicHostname(hostname);
}

function isPublicHTTPSURL(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      url.protocol === 'https:'
      && url.username === ''
      && url.password === ''
      && isPlausiblePublicHostname(url.hostname)
    );
  } catch {
    return false;
  }
}

function isPlausiblePublicHostname(value: string): boolean {
  const hostname = value.trim().toLowerCase().replace(/\.$/, '');
  if (
    !hostname.includes('.')
    || hostname.includes(':')
    || /^[\d.]+$/.test(hostname)
    || RESERVED_HOSTS.has(hostname)
    || RESERVED_HOST_SUFFIXES.some((suffix) => hostname.endsWith(suffix))
  ) {
    return false;
  }

  return hostname.split('.').every((label) => (
    label.length > 0
    && label.length <= 63
    && /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(label)
  ));
}
