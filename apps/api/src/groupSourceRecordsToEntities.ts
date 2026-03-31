import type { Entity, SourceRecord } from '@electronic-mail/types';

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
  const recordsByThreadId = new Map<string, SourceRecord[]>();

  for (const record of records) {
    const existing = recordsByThreadId.get(record.thread_id);

    if (existing) {
      existing.push(record);
      continue;
    }

    recordsByThreadId.set(record.thread_id, [record]);
  }

  return Array.from(recordsByThreadId.values()).map((groupedRecords) => {
    const sortedRecords = [...groupedRecords].sort((left, right) =>
      left.received_at.localeCompare(right.received_at),
    );
    const firstRecord = sortedRecords[0];
    const lastRecord = sortedRecords[sortedRecords.length - 1];
    const combinedText = sortedRecords
      .flatMap((record) => collectStringValues(record.raw_payload))
      .join(' ')
      .toLowerCase();
    const currentState = deriveCurrentState(sortedRecords, combinedText);
    const dueAt = extractDueAt(sortedRecords, combinedText);
    const importance =
      hasHumanSender(sortedRecords) ||
      dueAt !== null ||
      hasPattern(combinedText, FINANCIAL_PATTERNS) ||
      isCalendarRelated(sortedRecords, combinedText);

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
  });
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
