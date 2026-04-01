import type { Entity, SourceRecord } from '@decision-pipeline/types';

const GROUPING_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;

const GROUPING_SUBJECT_KEYWORDS = ['bill', 'payment', 'due', 'statement'];

const RSVP_PATTERNS = [
  'rsvp',
  'accept or decline',
  'calendar invite',
  'meeting invite',
  'invited you',
];

const REPLY_PATTERNS = [
  'please reply',
  'reply needed',
  'respond by',
  'can you',
  'could you',
  'let me know',
  'awaiting your response',
  'action required',
];

const COMPLETED_PATTERNS = [
  'completed',
  'resolved',
  'closed',
  'paid',
  'payment received',
  'done',
  'finished',
  'accepted',
  'declined',
];

const CALENDAR_PATTERNS = ['calendar', 'meeting', 'event', 'invite', 'invitation', 'rsvp'];

const FINANCIAL_PATTERNS = [
  'invoice',
  'bill',
  'payment',
  'amount due',
  'receipt',
  'renewal',
  'subscription',
  'refund',
  'balance',
];

const SYSTEM_SENDER_PATTERNS = [
  'no-reply',
  'noreply',
  'do-not-reply',
  'donotreply',
  'notification',
  'notifications@',
  'mailer-daemon',
  'newsletter',
  'updates@',
  'support@',
  'calendar-notification',
];

export function groupSourceRecordsToEntities(records: SourceRecord[]): Entity[] {
  const groupedRecords: Array<{
    records: SourceRecord[];
  }> = [];

  const sortedRecords = [...records].sort(
    (left, right) =>
      left.received_at.localeCompare(right.received_at) || left.id.localeCompare(right.id),
  );

  for (const record of sortedRecords) {
    const existing = groupedRecords.find(
      (group) => canMergeRecordIntoGroup(group.records, record),
    );

    if (existing !== undefined) {
      existing.records.push(record);
    } else {
      groupedRecords.push({
        records: [record],
      });
    }
  }

  return groupedRecords.map((group) => {
    const recordsInGroup = [...group.records].sort((left, right) =>
      left.received_at.localeCompare(right.received_at),
    );
    const firstRecord = recordsInGroup[0];
    const lastRecord = recordsInGroup[recordsInGroup.length - 1];
    const rawCombinedText = recordsInGroup
      .flatMap((record) => collectStringValues(record.raw_payload))
      .join(' ');
    const normalizedText = rawCombinedText.toLowerCase();
    const currentState = deriveCurrentState(recordsInGroup, normalizedText);
    const dueAt = extractDueAt(recordsInGroup, rawCombinedText);
    const importance =
      hasHumanSender(recordsInGroup) ||
      dueAt !== null ||
      hasPattern(normalizedText, FINANCIAL_PATTERNS) ||
      isCalendarRelated(recordsInGroup, normalizedText);
    const threadIds = new Set(recordsInGroup.map((record) => record.thread_id));

    if (threadIds.size === 1) {
      return {
        id: firstRecord.thread_id,
        user_id: firstRecord.user_id,
        thread_id: firstRecord.thread_id,
        current_state: currentState,
        due_at: dueAt,
        importance,
        lifecycle_state: currentState === 'completed' ? 'resolved' : 'active',
        created_at: firstRecord.received_at,
        updated_at: lastRecord.received_at,
      };
    }

    const groupId = buildCrossThreadEntityId(group.records);

    return {
      id: groupId,
      user_id: firstRecord.user_id,
      group_id: groupId,
      current_state: currentState,
      due_at: dueAt,
      importance,
      lifecycle_state: currentState === 'completed' ? 'resolved' : 'active',
      created_at: firstRecord.received_at,
      updated_at: lastRecord.received_at,
    };
  });
}

export function computeNormalizedGroupId(record: SourceRecord): string {
  const senderDomain = extractSenderDomain(record);
  const subjectTokens = extractSubjectGroupingTokens(record);

  if (senderDomain === null || subjectTokens.length === 0) {
    return `thread:${record.thread_id}`;
  }

  return `group:${senderDomain}:${subjectTokens.join(':')}`;
}

function deriveCurrentState(records: SourceRecord[], combinedText: string): string {
  if (hasPattern(combinedText, COMPLETED_PATTERNS)) {
    return 'completed';
  }

  if (isCalendarRelated(records, combinedText) || hasPattern(combinedText, RSVP_PATTERNS)) {
    return 'awaiting_rsvp';
  }

  if (hasPattern(combinedText, REPLY_PATTERNS)) {
    return 'awaiting_reply';
  }

  return 'informational';
}

function extractDueAt(records: SourceRecord[], combinedText: string): string | null {
  const keyMatches = records.flatMap((record) =>
    findValuesForKeys(record.raw_payload, /due|deadline|date|start|end|time|when/i),
  );

  for (const candidate of keyMatches) {
    const extracted = extractDateString(candidate);

    if (extracted !== null) {
      return extracted;
    }
  }

  return extractDateString(combinedText);
}

function hasHumanSender(records: SourceRecord[]): boolean {
  const senderValues = records.flatMap((record) =>
    findValuesForKeys(record.raw_payload, /from|sender|email/i),
  );

  return senderValues.some((sender) => {
    const normalizedSender = sender.toLowerCase();

    if (!normalizedSender.includes('@')) {
      return false;
    }

    return !SYSTEM_SENDER_PATTERNS.some((pattern) => normalizedSender.includes(pattern));
  });
}

function isCalendarRelated(records: SourceRecord[], combinedText: string): boolean {
  if (records.some((record) => record.source === 'calendar')) {
    return true;
  }

  return hasPattern(combinedText, CALENDAR_PATTERNS);
}

function hasPattern(value: string, patterns: readonly string[]): boolean {
  return patterns.some((pattern) => value.includes(pattern));
}

function collectStringValues(value: unknown): string[] {
  if (typeof value === 'string') {
    return [value];
  }

  if (Array.isArray(value)) {
    return value.flatMap((item) => collectStringValues(item));
  }

  if (typeof value === 'object' && value !== null) {
    return Object.values(value as Record<string, unknown>).flatMap((item) =>
      collectStringValues(item),
    );
  }

  return [];
}

function findValuesForKeys(value: unknown, pattern: RegExp): string[] {
  if (Array.isArray(value)) {
    return value.flatMap((item) => findValuesForKeys(item, pattern));
  }

  if (typeof value !== 'object' || value === null) {
    return [];
  }

  const values: string[] = [];

  for (const [key, entryValue] of Object.entries(value as Record<string, unknown>)) {
    if (pattern.test(key)) {
      values.push(...collectStringValues(entryValue));
    }

    values.push(...findValuesForKeys(entryValue, pattern));
  }

  return values;
}

function extractDateString(value: string): string | null {
  const isoDateTimeMatch = value.match(
    /\b\d{4}-\d{2}-\d{2}(?:[tT ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?\b/,
  );

  if (isoDateTimeMatch) {
    return isoDateTimeMatch[0];
  }

  const longDateMatch = value.match(
    /\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2},\s+\d{4}\b/i,
  );

  if (longDateMatch) {
    return longDateMatch[0];
  }

  return null;
}

function canMergeRecordIntoGroup(groupRecords: SourceRecord[], record: SourceRecord): boolean {
  const firstRecord = groupRecords[0];

  if (firstRecord.user_id !== record.user_id) {
    return false;
  }

  const groupNormalizedId = computeNormalizedGroupId(firstRecord);
  const recordNormalizedId = computeNormalizedGroupId(record);

  if (groupNormalizedId.startsWith('thread:') || recordNormalizedId.startsWith('thread:')) {
    return firstRecord.thread_id === record.thread_id;
  }

  const groupSenderDomain = extractSenderDomain(firstRecord);
  const recordSenderDomain = extractSenderDomain(record);

  if (groupSenderDomain === null || recordSenderDomain === null || groupSenderDomain !== recordSenderDomain) {
    return false;
  }

  const groupSubjectTokens = extractSubjectGroupingTokens(firstRecord);
  const recordSubjectTokens = extractSubjectGroupingTokens(record);

  if (!groupSubjectTokens.some((token) => recordSubjectTokens.includes(token))) {
    return false;
  }

  const recordTimestamp = Date.parse(record.received_at);
  const latestGroupTimestamp = Date.parse(groupRecords[groupRecords.length - 1].received_at);

  if (
    !Number.isNaN(recordTimestamp) &&
    !Number.isNaN(latestGroupTimestamp) &&
    Math.abs(recordTimestamp - latestGroupTimestamp) > GROUPING_WINDOW_MS
  ) {
    return false;
  }

  const groupDueAt = extractDueAtForRecordSet(groupRecords);
  const recordDueAt = extractDueAtForRecord(record);

  if (groupDueAt !== null && recordDueAt !== null && areClearlyDifferentDates(groupDueAt, recordDueAt)) {
    return false;
  }

  const groupAccountReference = extractAccountReferenceForRecordSet(groupRecords);
  const recordAccountReference = extractAccountReference(record);

  if (
    groupAccountReference !== null &&
    recordAccountReference !== null &&
    groupAccountReference !== recordAccountReference
  ) {
    return false;
  }

  return true;
}

function buildCrossThreadEntityId(records: SourceRecord[]): string {
  const baseGroupId = computeNormalizedGroupId(records[0]).replace(/^group:/, '');
  const dueAt = extractDueAtForRecordSet(records);
  const accountReference = extractAccountReferenceForRecordSet(records);
  const parts = [`group:${baseGroupId}`];

  if (accountReference !== null) {
    parts.push(`ref:${normalizeIdFragment(accountReference)}`);
  }

  if (dueAt !== null) {
    parts.push(`due:${normalizeIdFragment(dueAt)}`);
  }

  if (parts.length === 1) {
    parts.push(`window:${normalizeIdFragment(records[0].received_at.slice(0, 10))}`);
  }

  return parts.join(':');
}

function extractSenderDomain(record: SourceRecord): string | null {
  const senderValues = findValuesForKeys(record.raw_payload, /from|sender|email/i);

  for (const senderValue of senderValues) {
    const match = senderValue.match(/[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})/i);

    if (match !== null) {
      return match[1].toLowerCase();
    }
  }

  return null;
}

function extractSubjectGroupingTokens(record: SourceRecord): string[] {
  const subjectValues = findValuesForKeys(record.raw_payload, /subject/i);
  const normalizedSubject = subjectValues.join(' ').toLowerCase().replace(/\b(?:(?:re|fw|fwd)\s*:\s*)+/g, ' ');
  const subjectTokens: string[] = normalizedSubject.match(/[a-z]+/g) ?? [];
  const matchedKeywords = GROUPING_SUBJECT_KEYWORDS.filter((keyword) =>
    subjectTokens.includes(keyword),
  );

  return [...new Set(matchedKeywords)].sort();
}

function extractDueAtForRecord(record: SourceRecord): string | null {
  const rawCombinedText = collectStringValues(record.raw_payload).join(' ');

  return extractDueAt([record], rawCombinedText);
}

function extractDueAtForRecordSet(records: SourceRecord[]): string | null {
  const rawCombinedText = records.flatMap((record) => collectStringValues(record.raw_payload)).join(' ');

  return extractDueAt(records, rawCombinedText);
}

function areClearlyDifferentDates(left: string, right: string): boolean {
  const leftTimestamp = Date.parse(left);
  const rightTimestamp = Date.parse(right);

  if (!Number.isNaN(leftTimestamp) && !Number.isNaN(rightTimestamp)) {
    return leftTimestamp !== rightTimestamp;
  }

  return left !== right;
}

function extractAccountReference(record: SourceRecord): string | null {
  const combinedText = collectStringValues(record.raw_payload).join(' ');
  const match = combinedText.match(
    /\b(?:account|acct|a\/c|card|statement|invoice|bill)(?:\s*(?:number|no|#|ending))?\s*[:#-]?\s*([A-Z0-9-]{4,})\b/i,
  );

  return match === null ? null : match[1].toLowerCase();
}

function extractAccountReferenceForRecordSet(records: SourceRecord[]): string | null {
  for (const record of records) {
    const reference = extractAccountReference(record);

    if (reference !== null) {
      return reference;
    }
  }

  return null;
}

function normalizeIdFragment(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}
