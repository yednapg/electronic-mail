import type {
  ActionConfidence,
  ActionType,
  AttentionItem,
  EffortLevel,
  Entity,
  NeedType,
  PipelineOutput,
} from '@electronic-mail/types';

export function classifyEntity(entity: Entity): PipelineOutput {
  if (entity.lifecycle_state === 'resolved') {
    return {
      entity,
      attention_item: null,
      suppressed: true,
      suppression_reason: 'resolved',
    };
  }

  const derivedNeedType = deriveNeedType(entity);
  const highImportanceOverride =
    derivedNeedType !== 'decision' &&
    entity.importance &&
    entity.current_state !== 'informational' &&
    entity.current_state !== 'completed';
  const needType: NeedType = highImportanceOverride ? 'decision' : derivedNeedType;
  const decision = highImportanceOverride
    ? {
        actionType: 'external' as const,
        effortLevel: 'deep' as const,
        actionConfidence: 'medium' as const,
        primaryAction: 'open',
      }
    : deriveDecisionDetails(entity);
  const clearActionExists = decision.actionType !== 'none';
  const shouldCreateAttentionItem = needType === 'decision' && (clearActionExists || entity.importance);

  if (!shouldCreateAttentionItem) {
    return {
      entity,
      attention_item: null,
      suppressed: true,
      suppression_reason: needType === 'awareness' ? 'no_action_needed' : 'low_importance',
    };
  }

  const attentionItem: AttentionItem = {
    id: entity.id,
    entity_id: entity.id,
    user_id: entity.user_id,
    need_type: needType,
    action_type: decision.actionType,
    effort_level: decision.effortLevel,
    timing_band: 'today',
    action_confidence: decision.actionConfidence,
    primary_action: decision.primaryAction,
    fallback_action: 'open',
    title: deriveTitle(entity),
    why_this_is_here: deriveWhyThisIsHere(entity, needType, decision.primaryAction),
    trace_id: entity.id,
    created_at: entity.updated_at,
  };

  return {
    entity,
    attention_item: attentionItem,
    suppressed: false,
    suppression_reason: null,
  };
}

function deriveNeedType(entity: Entity): NeedType {
  if (entity.current_state === 'awaiting_reply') {
    return 'decision';
  }

  if (entity.current_state === 'awaiting_rsvp') {
    return 'decision';
  }

  if (entity.due_at !== null) {
    return 'decision';
  }

  if (entity.current_state === 'informational') {
    return 'awareness';
  }

  if (entity.current_state === 'completed') {
    return 'awareness';
  }

  return 'awareness';
}

function deriveDecisionDetails(entity: Entity): {
  actionType: ActionType;
  effortLevel: EffortLevel;
  actionConfidence: ActionConfidence;
  primaryAction: string;
} {
  if (entity.current_state === 'awaiting_reply') {
    return {
      actionType: 'inline',
      effortLevel: 'quick',
      actionConfidence: 'high',
      primaryAction: 'reply',
    };
  }

  if (entity.current_state === 'awaiting_rsvp') {
    return {
      actionType: 'inline',
      effortLevel: 'quick',
      actionConfidence: 'high',
      primaryAction: 'confirm',
    };
  }

  if (entity.due_at !== null) {
    return {
      actionType: entity.importance ? 'external' : 'none',
      effortLevel: entity.importance ? 'deep' : 'quick',
      actionConfidence: entity.importance ? 'medium' : 'low',
      primaryAction: entity.importance ? 'open' : 'none',
    };
  }

  if (entity.importance) {
    return {
      actionType: 'external',
      effortLevel: 'deep',
      actionConfidence: 'low',
      primaryAction: 'open',
    };
  }

  return {
    actionType: 'none',
    effortLevel: 'quick',
    actionConfidence: 'low',
    primaryAction: 'none',
  };
}

function deriveTitle(entity: Entity): string {
  if (entity.current_state === 'awaiting_reply') {
    return 'Reply needed';
  }

  if (entity.current_state === 'awaiting_rsvp') {
    return 'RSVP needed';
  }

  if (entity.due_at !== null) {
    return `Due ${entity.due_at}`;
  }

  if (entity.current_state === 'completed') {
    return 'Completed';
  }

  return 'Informational item';
}

function deriveWhyThisIsHere(
  entity: Entity,
  needType: NeedType,
  primaryAction: string,
): string {
  if (entity.current_state === 'awaiting_reply') {
    return 'This is here because the thread appears to need a reply.';
  }

  if (entity.current_state === 'awaiting_rsvp') {
    return 'This is here because the thread appears to need an RSVP.';
  }

  if (entity.due_at !== null) {
    return `This is here because a due date was detected for ${entity.due_at}.`;
  }

  if (needType === 'decision' && primaryAction === 'open') {
    return 'This is here because it appears important but needs review outside the app.';
  }

  return 'This is here because the item may need attention.';
}
