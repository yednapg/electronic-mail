import type { GmailThreadRow, MailboxResponse, SmartInboxResponse, SmartInboxRow } from './types';

const SMART_ROW_READER_PREFIX = 'smart-row:';
const MAILBOX_DISPLAY_CLUSTER_PREFIX = 'mailbox-cluster:';

export function mailboxFromSmartInbox(
  smartInbox: SmartInboxResponse | null | undefined,
  fallbackMailbox: MailboxResponse | null | undefined,
): MailboxResponse | null {
  if (!hasSmartInboxRows(smartInbox)) {
    return fallbackMailbox ?? null;
  }

  const sections = smartInbox.sections
    .map((section) => ({
      id: section.id,
      title: section.title,
      rows: section.rows.map((row) => smartRowToMailboxRow(row, smartInbox)),
    }))
    .filter((section) => section.rows.length > 0);
  const rowCount = sections.reduce((total, section) => total + section.rows.length, 0);

  return {
    label: fallbackMailbox?.label ?? 'inbox',
    total_threads: Math.max(smartInbox.total_rows, rowCount),
    next_cursor: fallbackMailbox?.next_cursor ?? null,
    loaded_threads: rowCount,
    window_days: smartInbox.hot_window_days,
    sections,
    ready_count: smartInbox.ready_count,
    pending_count: smartInbox.partial_count + smartInbox.failed_count,
    oldest_imported_at: fallbackMailbox?.oldest_imported_at ?? null,
    full_import_running: fallbackMailbox?.full_import_running,
    full_import_completed: fallbackMailbox?.full_import_completed,
  };
}

export function hasSmartInboxRows(smartInbox: SmartInboxResponse | null | undefined): smartInbox is SmartInboxResponse {
  return Boolean(smartInbox && smartInbox.total_rows > 0 && smartInbox.sections.some((section) => section.rows.length > 0));
}

export function smartRowReaderThreadId(row: SmartInboxRow): string {
  const explicitReaderID = cleanValue(row.reader_thread_id);
  if (explicitReaderID) {
    return explicitReaderID;
  }

  const sourceThreadIDs = row.source_thread_ids.filter((threadID) => cleanValue(threadID));
  const sourceMessageIDs = row.source_message_ids.filter((messageID) => cleanValue(messageID));
  const grouped =
    row.row_type === 'verified_group'
    || row.row_type === 'related_bundle'
    || sourceThreadIDs.length > 1
    || sourceMessageIDs.length > 1
    || row.row_key.startsWith(MAILBOX_DISPLAY_CLUSTER_PREFIX);
  if (grouped) {
    return `${SMART_ROW_READER_PREFIX}${row.id}`;
  }

  return cleanValue(sourceThreadIDs[0]) ?? cleanValue(row.row_key) ?? row.id;
}

function smartRowToMailboxRow(row: SmartInboxRow, smartInbox: SmartInboxResponse): GmailThreadRow {
  const readerThreadID = smartRowReaderThreadId(row);
  const title = cleanValue(row.title) ?? 'Untitled mail';
  const latestMessageAt = cleanValue(row.latest_message_at) ?? cleanValue(smartInbox.generated_at) ?? '';
  const latestMessageID = cleanValue(row.latest_message_id) ?? cleanValue(row.source_message_ids[0]) ?? readerThreadID;
  const sourceCount = Math.max(row.source_message_ids.length, row.source_thread_ids.length, 1);
  const ready = row.readiness === 'ready';

  return {
    thread_id: readerThreadID,
    entity_id: row.id,
    title,
    latest_source_record_id: latestMessageID,
    latest_received_at: latestMessageAt,
    latest_message_at: latestMessageAt,
    latest_subject: title,
    latest_sender: row.primary_sender ?? null,
    sender: row.primary_sender ?? null,
    participants: row.primary_sender ? [row.primary_sender] : [],
    message_count: sourceCount,
    summary: null,
    ai_group_id: row.id,
    ai_title: title,
    ai_summary: null,
    snippet: null,
    label_ids: ['INBOX'],
    labels: ['INBOX'],
    unread: row.readiness === 'partial' || row.readiness === 'stale',
    action_needed: row.action_type !== 'none',
    action_type: row.action_type,
    action_type_key: row.action_type,
    priority: row.priority,
    dashboard_visible: row.action_type !== 'none',
    current_state: row.action_type === 'none' ? 'waiting' : 'open',
    lifecycle_state: 'active',
    outcome_type: null,
    lifecycle_updates: [],
    children: [],
    enrichment_status: ready ? 'ready' : row.readiness === 'failed' ? 'failed' : 'pending',
    presentation_status: ready ? 'ai_ready' : row.readiness === 'failed' ? 'fallback' : 'ai_pending',
  };
}

function cleanValue(value: string | null | undefined): string | null {
  const cleaned = value?.trim();
  return cleaned ? cleaned : null;
}
