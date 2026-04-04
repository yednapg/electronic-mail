import type { Entity as PrismaEntity, EntityState, PrismaClient, SourceRecord as PersistedSourceRecord } from '@prisma/client';

import { prisma } from './db';

const MONTH_PATTERN =
  /\b(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s+\d{1,2}(?:,\s*\d{4})?\b/i;

type DerivedState = {
  currentState: string;
  dueAt: Date | null;
};

export async function deriveEntityState(
  entity: Pick<PrismaEntity, 'id'>,
  records: PersistedSourceRecord[],
  client: PrismaClient = prisma,
): Promise<EntityState> {
  const derived = deriveState(records);

  return client.entityState.upsert({
    where: {
      entityId: entity.id,
    },
    create: {
      entityId: entity.id,
      currentState: derived.currentState,
      dueAt: derived.dueAt,
    },
    update: {
      currentState: derived.currentState,
      dueAt: derived.dueAt,
    },
  });
}

export function deriveState(records: PersistedSourceRecord[]): DerivedState {
  const sortedRecords = [...records].sort((left, right) => right.timestamp.getTime() - left.timestamp.getTime());
  let currentState = sortedRecords[0]?.source === 'calendar' ? 'scheduled' : 'resolved';

  for (const record of sortedRecords) {
    const nextState = classifyRecordState(getRecordText(record), record.source);

    if (nextState !== null) {
      currentState = nextState;
      break;
    }
  }

  const dueAt = findLatestRelevantDueAt(sortedRecords, currentState);

  return { currentState, dueAt };
}

function classifyRecordState(text: string, source: string): string | null {
  if (/\b(completed|paid)\b/i.test(text)) {
    return 'resolved';
  }

  if (/\b(confirmed|scheduled)\b/i.test(text) || source === 'calendar') {
    return 'scheduled';
  }

  if (/\b(reply|let me know)\b/i.test(text)) {
    return 'awaiting_reply';
  }

  if (/\b(due|expires|deadline)\b/i.test(text)) {
    return 'pending_deadline';
  }

  return null;
}

function findLatestRelevantDueAt(
  records: PersistedSourceRecord[],
  currentState: string,
): Date | null {
  for (const record of records) {
    const text = getRecordText(record);

    if (currentState === 'pending_deadline' && !/\b(due|expires|deadline)\b/i.test(text)) {
      continue;
    }

    if (currentState === 'scheduled' && !(/\b(confirmed|scheduled)\b/i.test(text) || record.source === 'calendar')) {
      continue;
    }

    const dueAt = extractDueAt(record);

    if (dueAt !== null) {
      return dueAt;
    }
  }

  return null;
}

function extractDueAt(record: PersistedSourceRecord): Date | null {
  const payload = getPayload(record);
  const explicitValue =
    getPayloadString(payload, 'start') ??
    getPayloadString(payload, 'due_at') ??
    getPayloadString(payload, 'dueAt');

  if (explicitValue !== null) {
    const explicitDate = toValidDate(explicitValue);

    if (explicitDate !== null) {
      return explicitDate;
    }
  }

  const match = getRecordText(record).match(MONTH_PATTERN);

  if (match === null) {
    return null;
  }

  const year = match[0].includes(',') ? '' : `, ${record.timestamp.getUTCFullYear()}`;

  return toValidDate(`${match[0]}${year}`);
}

function getRecordText(record: PersistedSourceRecord): string {
  const payload = getPayload(record);
  const parts = [
    record.subject ?? '',
    getPayloadString(payload, 'subject') ?? '',
    getPayloadString(payload, 'body') ?? '',
  ];

  return parts.join(' ').trim();
}

function getPayload(record: PersistedSourceRecord): Record<string, unknown> {
  return typeof record.rawPayload === 'object' && record.rawPayload !== null
    ? (record.rawPayload as Record<string, unknown>)
    : {};
}

function getPayloadString(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];

  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : null;
}

function toValidDate(value: string): Date | null {
  const parsed = Date.parse(value);

  if (Number.isNaN(parsed)) {
    return null;
  }

  return new Date(parsed);
}
