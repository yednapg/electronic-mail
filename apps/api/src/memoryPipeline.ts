import type {
  AttentionItem,
  Entity as PipelineEntity,
  FeedResponse,
  PipelineOutput,
  SourceRecord,
  SourceType,
  TimingBand,
} from '@electronic-mail/types';
import type {
  Entity as PersistedEntity,
  EntityMember,
  EntityState,
  PrismaClient,
  SourceRecord as PersistedSourceRecord,
} from '@prisma/client';

import { buildFeed } from './buildFeed';
import { prisma } from './db';
import { deriveEntityState } from './deriveEntityState';
import { resolveEntityForRecord, toPipelineSourceRecord } from './entityResolver';

type LoadedEntity = PersistedEntity & {
  state: EntityState | null;
  members: Array<EntityMember & { sourceRecord: PersistedSourceRecord }>;
};

export async function hydratePersistentMemory(
  sourceRecords: SourceRecord[] = [],
  client: PrismaClient = prisma,
): Promise<void> {
  const touchedEntityIds = new Set<string>();

  for (const record of [...sourceRecords].sort((left, right) => left.received_at.localeCompare(right.received_at))) {
    const entity = await resolveEntityForRecord(record, client);
    touchedEntityIds.add(entity.id);
  }

  const unlinkedRecords = await client.sourceRecord.findMany({
    where: {
      entityMembers: {
        none: {},
      },
    },
    orderBy: {
      timestamp: 'asc',
    },
  });

  for (const record of unlinkedRecords) {
    const entity = await resolveEntityForRecord(toPipelineSourceRecord(record), client);
    touchedEntityIds.add(entity.id);
  }

  const entitiesMissingState = await client.entity.findMany({
    where: {
      state: null,
    },
    select: {
      id: true,
    },
  });

  for (const entity of entitiesMissingState) {
    touchedEntityIds.add(entity.id);
  }

  for (const entityId of touchedEntityIds) {
    const loadedEntity = await client.entity.findUnique({
      where: { id: entityId },
      include: {
        members: {
          include: {
            sourceRecord: true,
          },
        },
      },
    });

    if (loadedEntity === null) {
      continue;
    }

    await deriveEntityState(loadedEntity, loadedEntity.members.map((member) => member.sourceRecord), client);
  }
}

export async function buildFeedFromEntities(
  currentTime: string,
  client: PrismaClient = prisma,
): Promise<FeedResponse> {
  const entities = await client.entity.findMany({
    include: {
      state: true,
      members: {
        include: {
          sourceRecord: true,
        },
      },
    },
  });

  const outputs = entities.map((entity) => toPipelineOutput(entity, currentTime));

  return buildFeed(outputs);
}

function toPipelineOutput(entity: LoadedEntity, currentTime: string): PipelineOutput {
  const latestRecord = getLatestRecord(entity.members);
  const state = entity.state;

  if (latestRecord === null || state === null) {
    return {
      entity: toPipelineEntity(entity, null, null),
      attention_item: null,
      suppressed: true,
      suppression_reason: 'missing_state',
    };
  }

  const pipelineEntity = toPipelineEntity(entity, state, latestRecord);

  if (state.currentState === 'resolved') {
    return {
      entity: pipelineEntity,
      attention_item: null,
      suppressed: true,
      suppression_reason: 'resolved',
    };
  }

  return {
    entity: pipelineEntity,
    attention_item: toAttentionItem(entity, state, latestRecord, currentTime),
    suppressed: false,
    suppression_reason: null,
  };
}

function toPipelineEntity(
  entity: PersistedEntity,
  state: EntityState | null,
  latestRecord: PersistedSourceRecord | null,
): PipelineEntity {
  return {
    id: entity.id,
    group_id: entity.id,
    user_id: 'local-user',
    source: (latestRecord?.source as SourceType | undefined) ?? 'gmail',
    current_state: state?.currentState ?? 'resolved',
    due_at: state?.dueAt?.toISOString() ?? null,
    importance: state?.currentState === 'pending_deadline' || state?.currentState === 'awaiting_reply',
    lifecycle_state: toLifecycleState(state?.currentState ?? 'resolved'),
    created_at: entity.createdAt.toISOString(),
    updated_at: entity.updatedAt.toISOString(),
  };
}

function toAttentionItem(
  entity: PersistedEntity,
  state: EntityState,
  latestRecord: PersistedSourceRecord,
  currentTime: string,
): AttentionItem {
  const primaryAction = toPrimaryAction(state.currentState);
  const title = latestRecord.subject ?? 'Untitled';

  return {
    id: entity.id,
    entity_id: entity.id,
    user_id: 'local-user',
    need_type: state.currentState === 'scheduled' ? 'awareness' : 'decision',
    action_type:
      primaryAction === 'reply' ? 'inline' : primaryAction === 'none' ? 'none' : 'external',
    effort_level: primaryAction === 'reply' || primaryAction === 'none' ? 'quick' : 'deep',
    timing_band: deriveTimingBand(state, currentTime, latestRecord.source),
    action_confidence: state.currentState === 'scheduled' ? 'high' : 'medium',
    primary_action: primaryAction,
    fallback_action: 'open',
    title,
    why_this_is_here: toWhyThisIsHere(state.currentState, title),
    due_at: state.dueAt?.toISOString() ?? null,
    importance_level: toImportanceLevel(state.currentState),
    lifecycle_state: toLifecycleState(state.currentState),
    current_state: state.currentState,
    source: latestRecord.source as SourceType,
    trace_id: entity.id,
    created_at: latestRecord.timestamp.toISOString(),
  };
}

function getLatestRecord(
  members: Array<EntityMember & { sourceRecord: PersistedSourceRecord }>,
): PersistedSourceRecord | null {
  const sortedMembers = [...members].sort(
    (left, right) => right.sourceRecord.timestamp.getTime() - left.sourceRecord.timestamp.getTime(),
  );

  return sortedMembers[0]?.sourceRecord ?? null;
}

function toPrimaryAction(currentState: string): string {
  if (currentState === 'awaiting_reply') {
    return 'reply';
  }

  if (currentState === 'pending_deadline') {
    return 'review';
  }

  if (currentState === 'scheduled') {
    return 'none';
  }

  return 'open';
}

function toWhyThisIsHere(currentState: string, title: string): string {
  if (currentState === 'pending_deadline') {
    return `This still has an upcoming deadline for ${title}.`;
  }

  if (currentState === 'awaiting_reply') {
    return `This thread is waiting on your reply about ${title}.`;
  }

  if (currentState === 'scheduled') {
    return `This is scheduled for ${title}.`;
  }

  return `This still matters for ${title}.`;
}

function toImportanceLevel(currentState: string): 'high' | 'medium' | 'low' {
  if (currentState === 'pending_deadline') {
    return 'high';
  }

  if (currentState === 'awaiting_reply') {
    return 'medium';
  }

  return 'low';
}

function toLifecycleState(currentState: string): 'active' | 'scheduled' | 'resolved' | 'suppressed' {
  if (currentState === 'scheduled') {
    return 'scheduled';
  }

  if (currentState === 'resolved') {
    return 'resolved';
  }

  return 'active';
}

function deriveTimingBand(
  state: EntityState,
  currentTime: string,
  source: string,
): TimingBand {
  const dueAt = state.dueAt;

  if (dueAt === null) {
    return source === 'calendar' || state.currentState === 'awaiting_reply' ? 'today' : 'later';
  }

  const currentTimestamp = Date.parse(currentTime);
  const dueTimestamp = dueAt.getTime();

  if (Number.isNaN(currentTimestamp)) {
    return 'today';
  }

  const deltaMs = dueTimestamp - currentTimestamp;

  if (deltaMs <= 24 * 60 * 60 * 1000) {
    return 'now';
  }

  if (deltaMs <= 3 * 24 * 60 * 60 * 1000) {
    return 'today';
  }

  return 'later';
}
