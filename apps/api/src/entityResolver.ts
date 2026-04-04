import type { Entity as PrismaEntity, PrismaClient, SourceRecord as PersistedSourceRecord } from '@prisma/client';
import type { SourceRecord } from '@electronic-mail/types';

import { prisma } from './db';

const SUBJECT_NOISE_PATTERN =
  /\b(confirmed|confirmation|accepted|waitlist(?:ed)?|rsvp|registered|registration|invite|invitation|reminder|update|notification|status)\b/g;

export async function resolveEntityForRecord(
  record: SourceRecord,
  client: PrismaClient = prisma,
): Promise<PrismaEntity> {
  const existingByMember = await client.entity.findFirst({
    where: {
      members: {
        some: {
          sourceRecordId: record.id,
        },
      },
    },
  });

  if (existingByMember !== null) {
    return existingByMember;
  }

  if (record.thread_id.length > 0) {
    const existingByThread = await client.entity.findFirst({
      where: {
        members: {
          some: {
            sourceRecord: {
              threadId: record.thread_id,
            },
          },
        },
      },
    });

    if (existingByThread !== null) {
      await attachRecordToEntity(existingByThread.id, record.id, client);
      return existingByThread;
    }
  }

  const subjectMatch = await findEntityBySubjectAndDomain(record, client);

  if (subjectMatch !== null) {
    await attachRecordToEntity(subjectMatch.id, record.id, client);
    return subjectMatch;
  }

  const canonicalKey = buildCanonicalKey(record);
  const existingByCanonicalKey = await client.entity.findUnique({
    where: { canonicalKey },
  });

  if (existingByCanonicalKey !== null) {
    await attachRecordToEntity(existingByCanonicalKey.id, record.id, client);
    return existingByCanonicalKey;
  }

  const entity = await client.entity.create({
    data: {
      canonicalKey,
    },
  });

  await attachRecordToEntity(entity.id, record.id, client);

  return entity;
}

export function normalizeSubject(subject: string): string {
  let normalized = subject.trim().toLowerCase();

  while (/^(re|fw|fwd):\s*/i.test(normalized)) {
    normalized = normalized.replace(/^(re|fw|fwd):\s*/i, '');
  }

  return normalized
    .replace(SUBJECT_NOISE_PATTERN, ' ')
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

export function getSenderDomain(sender: string): string {
  const match = sender.toLowerCase().match(/@([a-z0-9.-]+\.[a-z]{2,})/);

  return match?.[1] ?? '';
}

function buildCanonicalKey(record: SourceRecord): string {
  const subject = normalizeSubject(getStringValue(record.raw_payload, 'subject'));
  const senderDomain = getSenderDomain(
    getStringValue(record.raw_payload, 'from') || getStringValue(record.raw_payload, 'sender'),
  );

  return `${subject || 'untitled'}::${senderDomain || 'unknown'}`;
}

async function findEntityBySubjectAndDomain(
  record: SourceRecord,
  client: PrismaClient,
): Promise<PrismaEntity | null> {
  const normalizedSubject = normalizeSubject(getStringValue(record.raw_payload, 'subject'));
  const senderDomain = getSenderDomain(
    getStringValue(record.raw_payload, 'from') || getStringValue(record.raw_payload, 'sender'),
  );

  if (normalizedSubject.length === 0) {
    return null;
  }

  const candidates = await client.sourceRecord.findMany({
    where: {
      ...(senderDomain.length > 0
        ? {
            sender: {
              contains: senderDomain,
            },
          }
        : {}),
      subject: {
        not: null,
      },
      entityMembers: {
        some: {},
      },
    },
    include: {
      entityMembers: {
        include: {
          entity: true,
        },
      },
    },
    take: 50,
  });

  for (const candidate of candidates) {
    const candidateSubject = normalizeSubject(candidate.subject ?? '');

    if (!isSimilarSubject(normalizedSubject, candidateSubject)) {
      continue;
    }

    const entity = candidate.entityMembers[0]?.entity;

    if (entity !== undefined) {
      return entity;
    }
  }

  return null;
}

function isSimilarSubject(left: string, right: string): boolean {
  if (left.length === 0 || right.length === 0) {
    return false;
  }

  return left.includes(right) || right.includes(left);
}

async function attachRecordToEntity(
  entityId: string,
  sourceRecordId: string,
  client: PrismaClient,
): Promise<void> {
  await client.entityMember.upsert({
    where: {
      entityId_sourceRecordId: {
        entityId,
        sourceRecordId,
      },
    },
    create: {
      entityId,
      sourceRecordId,
    },
    update: {},
  });
}

function getStringValue(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];

  return typeof value === 'string' ? value.trim() : '';
}

export function toPipelineSourceRecord(record: PersistedSourceRecord): SourceRecord {
  return {
    id: record.id,
    user_id: 'local-user',
    source: record.source as SourceRecord['source'],
    thread_id: record.threadId ?? '',
    raw_payload: toRecordPayload(record.rawPayload),
    received_at: record.timestamp.toISOString(),
  };
}

function toRecordPayload(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {};
}
