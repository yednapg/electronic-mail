import type {
  AppSessionStateResponse,
  DashboardResponse,
  FeedItem,
  GmailThreadRow,
  MailboxResponse,
  ThreadReaderResponse,
} from './types';

const DEMO_NOW = '2026-05-16T09:30:00+05:30';
const DEMO_USER_ID = 'demo-user';

const rows: GmailThreadRow[] = [
  mailRow({
    id: 'demo-neo',
    title: 'Neo Residency application update',
    sender: 'Neo <neo-noreply@example.com>',
    subject: 'Neo Residency application update',
    receivedAt: '2026-05-16T08:45:00+05:30',
    messageCount: 4,
    summary: 'Neo has received the application, founder profile, reference, and latest decision update in one grouped thread.',
    actionNeeded: true,
    actionType: 'review',
    priority: 85,
    dashboardVisible: true,
    labels: ['INBOX', 'IMPORTANT', 'application'],
    updates: [
      ['demo-neo-4', '2026-05-16T08:45:00+05:30', 'Neo update / Futuristic Intelligence', 'Connor Ling <connor@neo.com>', 'Application decision update received.'],
      ['demo-neo-3', '2026-05-01T18:16:00+05:30', 'Reference received from Shivay Lamba', 'Neo <neo-noreply@example.com>', 'Reference was received.'],
      ['demo-neo-2', '2026-05-01T12:23:25+05:30', 'Neo application received: Futuristic Intelligence', 'Neo <neo-noreply@example.com>', 'Application was received.'],
      ['demo-neo-1', '2026-04-30T16:02:00+05:30', 'Welcome to the Neo application platform, TestUser!', 'Neo <neo-noreply@example.com>', 'Account setup started.'],
    ],
  }),
  mailRow({
    id: 'demo-calstate',
    title: 'Cal State Apply account welcome',
    sender: 'support@calstateapply.myliaison.com',
    subject: 'Welcome to the Cal State Apply application',
    receivedAt: '2026-05-15T13:26:59+05:30',
    messageCount: 1,
    summary: 'Cal State Apply created the application account and shared the application ID for future steps.',
    actionNeeded: false,
    actionType: 'none',
    priority: 35,
    dashboardVisible: false,
    labels: ['INBOX', 'IMPORTANT', 'application'],
  }),
  mailRow({
    id: 'demo-northstar',
    title: 'Northstar FX trading limit request',
    sender: 'NorthstarFXclearretail <northstarfx@northstar.example>',
    subject: 'Regarding trading limit request raised on 8-5-26',
    receivedAt: '2026-05-15T10:33:00+05:30',
    messageCount: 3,
    summary: 'Northstar replied on the trading limit request and needs review before the next FX Retail step.',
    actionNeeded: true,
    actionType: 'review',
    priority: 90,
    dashboardVisible: true,
    labels: ['INBOX', 'IMPORTANT', 'finance'],
  }),
  mailRow({
    id: 'demo-cedar-mobile',
    title: 'Cedar Mobile Black bill due soon',
    sender: 'Cedar Mobile <ebill@cedar-mobile.example>',
    subject: "Bill for your Cedar Mobile Black account - May'26",
    receivedAt: '2026-05-15T04:50:00+05:30',
    messageCount: 1,
    summary: 'Cedar Mobile generated the May bill. Review or pay it before the due date.',
    actionNeeded: true,
    actionType: 'pay',
    priority: 80,
    dashboardVisible: true,
    labels: ['INBOX', 'billing'],
  }),
  mailRow({
    id: 'demo-rbi',
    title: 'RBI Retail Direct account setup updates',
    sender: 'support@example-retailer.example',
    subject: 'RBI Retail Direct - Access to NDS OM is Granted',
    receivedAt: '2026-05-11T18:45:00+05:30',
    messageCount: 5,
    summary: 'RBI Retail Direct sent account setup, access, KYC, and virtual account updates.',
    actionNeeded: false,
    actionType: 'open',
    priority: 45,
    dashboardVisible: true,
    labels: ['INBOX', 'account'],
  }),
  mailRow({
    id: 'demo-github',
    title: 'GitHub Education approved',
    sender: 'GitHub Education <edu-github-noreply@example.com>',
    subject: '[GitHub Education] @demo-user',
    receivedAt: '2026-05-11T18:24:00+05:30',
    messageCount: 1,
    summary: 'GitHub Education approved the student benefits request.',
    actionNeeded: false,
    actionType: 'none',
    priority: 35,
    dashboardVisible: true,
    labels: ['INBOX', 'account'],
  }),
  mailRow({
    id: 'demo-yc-rsvp',
    title: 'Startup School India RSVP needed',
    sender: 'TestUser & Sam <events@ycombinator.com>',
    subject: 'RSVP within 72 hrs to confirm your spot for YC Startup School India',
    receivedAt: '2026-05-11T10:08:00+05:30',
    messageCount: 2,
    summary: 'YC Startup School India needs an RSVP confirmation within 72 hours.',
    actionNeeded: true,
    actionType: 'review',
    priority: 72,
    dashboardVisible: true,
    labels: ['INBOX', 'event'],
  }),
  mailRow({
    id: 'demo-apple-delivery',
    title: 'Apple order arriving today',
    sender: 'Apple Developer <no_reply@email.apple.com>',
    subject: 'Your Apple order for MacBook Pro is arriving today',
    receivedAt: '2026-05-10T12:42:00+05:30',
    messageCount: 1,
    summary: 'Apple says the MacBook Pro delivery is arriving today.',
    actionNeeded: false,
    actionType: 'open',
    priority: 42,
    dashboardVisible: false,
    labels: ['INBOX', 'shopping'],
  }),
  mailRow({
    id: 'demo-google-hsbc',
    title: 'HSBC Bank registered on FX-Retail',
    sender: 'Google <alerts@google.com>',
    subject: 'Your HSBC Bank has been successfully registered on FX-Retail',
    receivedAt: '2026-05-08T18:34:00+05:30',
    messageCount: 1,
    summary: 'HSBC Bank registration on FX-Retail is complete.',
    actionNeeded: false,
    actionType: 'open',
    priority: 38,
    dashboardVisible: false,
    labels: ['INBOX', 'finance'],
  }),
  mailRow({
    id: 'demo-michigan-aid',
    title: 'University of Michigan financial aid follow-up',
    sender: 'International Office <international@umich.edu>',
    subject: 'Financial aid document follow-up',
    receivedAt: '2026-05-07T19:26:00+05:30',
    messageCount: 2,
    summary: 'University of Michigan sent a financial aid follow-up.',
    actionNeeded: true,
    actionType: 'review',
    priority: 76,
    dashboardVisible: true,
    labels: ['INBOX', 'education'],
  }),
  mailRow({
    id: 'demo-speedrun',
    title: 'Speedrun application still needs submission',
    sender: 'Ryan Rigney <ryan@speedrun.com>',
    subject: 'Application still needs submission',
    receivedAt: '2026-05-06T23:02:00+05:30',
    messageCount: 1,
    summary: 'Speedrun says the application still needs to be submitted.',
    actionNeeded: true,
    actionType: 'review',
    priority: 82,
    dashboardVisible: true,
    labels: ['INBOX', 'application'],
  }),
  mailRow({
    id: 'demo-bhim',
    title: 'BHIM forex transaction support ticket',
    sender: 'BHIM Support <support@bhimupi.org.in>',
    subject: 'Forex transaction support ticket update',
    receivedAt: '2026-05-06T13:51:00+05:30',
    messageCount: 1,
    summary: 'BHIM sent an update on the forex transaction support ticket.',
    actionNeeded: false,
    actionType: 'open',
    priority: 36,
    dashboardVisible: true,
    labels: ['INBOX', 'support'],
  }),
  mailRow({
    id: 'demo-adobe-sign',
    title: 'FX Retail document signature request',
    sender: 'Kiran K via Adobe Acrobat Sign <echosign@echosign.com>',
    subject: 'Signature requested for FX Retail document',
    receivedAt: '2026-05-05T13:57:00+05:30',
    messageCount: 1,
    summary: 'Adobe Acrobat Sign has an FX Retail document waiting for signature.',
    actionNeeded: true,
    actionType: 'review',
    priority: 78,
    dashboardVisible: true,
    labels: ['INBOX', 'document'],
  }),
  mailRow({
    id: 'demo-neo-reference',
    title: 'Neo Residency reference received',
    sender: 'Neo <neo-noreply@example.com>',
    subject: 'Reference received from Shivay Lamba',
    receivedAt: '2026-05-01T18:16:00+05:30',
    messageCount: 1,
    summary: 'Neo received the reference from Shivay Lamba.',
    actionNeeded: false,
    actionType: 'open',
    priority: 44,
    dashboardVisible: false,
    labels: ['INBOX', 'application'],
  }),
  mailRow({
    id: 'demo-google-education',
    title: 'GitHub Education request approved',
    sender: 'Google <alerts@google.com>',
    subject: 'Your request to join GitHub Education has been approved',
    receivedAt: '2026-05-05T09:00:00+05:30',
    messageCount: 1,
    summary: 'The GitHub Education request has been approved.',
    actionNeeded: false,
    actionType: 'open',
    priority: 35,
    dashboardVisible: false,
    labels: ['INBOX', 'education'],
  }),
  mailRow({
    id: 'demo-yc-final',
    title: 'Startup School India event details',
    sender: 'TestUser & Sam <events@ycombinator.com>',
    subject: 'Startup School India event details',
    receivedAt: '2026-04-30T10:08:00+05:30',
    messageCount: 1,
    summary: 'YC sent the final event details for Startup School India.',
    actionNeeded: false,
    actionType: 'open',
    priority: 35,
    dashboardVisible: false,
    labels: ['INBOX', 'event'],
  }),
];

const dashboard: DashboardResponse = {
  auth: {
    available: true,
    connected: true,
    connect_url: null,
  },
  profile: {
    email: 'demo@example.com',
    display_name: 'TestUser',
  },
  briefing: {
    headline: 'Good morning, TestUser.',
    brief: 'You have two things to review, one bill to pay, and a few useful updates waiting.',
    parts: [
      { type: 'meetings', emoji: '', count: 0, text: 'meetings' },
      { type: 'tasks', emoji: '', count: 3, text: 'tasks' },
      { type: 'emails', emoji: '', count: 0, text: 'emails to reply' },
    ],
    important: {
      emoji: '',
      count: 1,
      text: 'Northstar FX trading limit request',
      mail_group_id: 'demo-northstar',
      action_type: 'review',
    },
    calendar_availability: {
      emoji: '',
      kind: 'no_meetings',
      time: null,
      text: 'clear on calendar today',
    },
  },
  feed: {
    now: [
      attentionItem(rows[2], 'Review the Northstar FX trading limit reply before continuing the request.', 'review', 'high', 'now'),
      attentionItem(rows[0], 'Check the Neo Residency update and decide whether anything needs follow-up.', 'review', 'high', 'now'),
    ],
    today: [
      attentionItem(rows[3], 'Cedar Mobile generated the May bill and it should be handled before the due date.', 'pay', 'medium', 'today'),
    ],
    worth_knowing: [
      attentionItem(rows[4], 'RBI Retail Direct completed several account setup steps.', 'open', 'medium', 'later'),
      attentionItem(rows[5], 'GitHub Education approved the request.', 'none', 'low', 'later'),
    ],
  },
  runtime_status: {
    feed_source: 'demo_fixture',
    canonical_ready: true,
    ai_groups_ready: true,
    pending_group_count: 0,
    ready_group_count: rows.length,
  },
};

const mailbox: MailboxResponse = {
  label: 'inbox',
  total_threads: rows.length,
  next_cursor: null,
  sections: [
    { id: 'yesterday-primary', title: 'Yesterday', rows: rows.slice(0, 4) },
    { id: 'yesterday-secondary', title: 'Yesterday', rows: rows.slice(4, 8) },
    { id: 'past-seven-days', title: 'Past 7 days', rows: rows.slice(8) },
  ],
  ready_count: rows.length,
  pending_count: 0,
  oldest_imported_at: '2026-04-30T16:02:00+05:30',
  full_import_running: false,
  full_import_completed: true,
};

const demoSession: AppSessionStateResponse = {
  user: {
    id: DEMO_USER_ID,
    email: 'demo@example.com',
    first_name: 'TestUser',
    display_name: 'TestUser',
  },
  readiness: {
    mode: 'returning',
    stage: 'welcome_back',
    ready_to_enter: true,
    dashboard_ready: true,
    mailbox_ready: true,
    ready_dashboard_count: dashboard.feed.now.length + dashboard.feed.today.length + dashboard.feed.worth_knowing.length,
    ready_mail_group_count: rows.length,
    full_import_running: false,
    full_import_completed: true,
    user_display_name: 'TestUser',
    error_message: null,
  },
  dashboard,
  mailbox,
  sync: {
    last_sync_at: DEMO_NOW,
    last_error: null,
    enrichment_pending_count: 0,
    ready_group_count: rows.length,
    oldest_imported_at: mailbox.oldest_imported_at,
    full_import_running: false,
    full_import_completed: true,
    full_import_completed_at: DEMO_NOW,
  },
};

const threads = Object.fromEntries(rows.map((row) => [row.thread_id, threadFromRow(row)]));

export function getDemoAppSession(): AppSessionStateResponse {
  return demoSession;
}

export function getDemoDashboard(): DashboardResponse {
  return dashboard;
}

export function getDemoMailbox(): MailboxResponse {
  return mailbox;
}

export function getDemoThreadReader(threadId: string): ThreadReaderResponse | null {
  return threads[threadId] ?? null;
}

function mailRow({
  id,
  title,
  sender,
  subject,
  receivedAt,
  messageCount,
  summary,
  actionNeeded,
  actionType,
  priority,
  dashboardVisible,
  labels,
  updates,
}: {
  id: string;
  title: string;
  sender: string;
  subject: string;
  receivedAt: string;
  messageCount: number;
  summary: string;
  actionNeeded: boolean;
  actionType: NonNullable<GmailThreadRow['action_type']>;
  priority: number;
  dashboardVisible: boolean;
  labels: string[];
  updates?: Array<[string, string, string, string, string]>;
}): GmailThreadRow {
  return {
    thread_id: id,
    entity_id: id,
    title,
    href: `/gmail/threads/${id}`,
    latest_source_record_id: `${id}-latest`,
    latest_received_at: receivedAt,
    latest_message_at: receivedAt,
    latest_subject: title,
    latest_sender: sender,
    sender,
    participants: [senderName(sender)],
    message_count: messageCount,
    summary,
    snippet: summary,
    label_ids: labels,
    labels,
    unread: actionNeeded,
    action_needed: actionNeeded,
    action_type: actionType,
    action_type_key: actionType,
    priority,
    dashboard_visible: dashboardVisible,
    current_state: actionNeeded ? 'open' : 'waiting',
    lifecycle_state: 'active',
    outcome_type: null,
    lifecycle_updates: (updates ?? [[`${id}-latest`, receivedAt, subject, sender, summary]]).map(([sourceId, updateReceivedAt, updateSubject, updateSender, updateSummary]) => ({
      source_record_id: sourceId,
      received_at: updateReceivedAt,
      subject: updateSubject,
      sender: updateSender,
      summary: updateSummary,
    })),
    enrichment_status: 'ready',
  };
}

function attentionItem(
  row: GmailThreadRow,
  why: string,
  primaryAction: string,
  importance: 'high' | 'medium' | 'low',
  timing: 'now' | 'today' | 'later',
): FeedItem {
  return {
    id: `${row.thread_id}-attention`,
    entity_id: row.thread_id,
    user_id: DEMO_USER_ID,
    need_type: 'decision',
    action_type: primaryAction === 'none' ? 'none' : 'external',
    effort_level: 'quick',
    timing_band: timing,
    action_confidence: importance === 'low' ? 'medium' : 'high',
    primary_action: primaryAction,
    fallback_action: 'open',
    title: row.title ?? row.latest_subject ?? 'Mail update',
    why_this_is_here: why,
    detail: {
      body: [row.summary ?? why],
      action_label: primaryAction === 'pay' ? 'Open bill' : primaryAction === 'review' ? 'Review email' : 'Read email',
      source_label: 'Gmail',
    },
    due_at: null,
    importance_level: importance,
    lifecycle_state: row.lifecycle_state ?? 'active',
    current_state: row.current_state ?? 'open',
    source: 'gmail',
    gmail_thread_id: row.thread_id,
    gmail_thread_action: null,
    trace_id: `${row.thread_id}-trace`,
    created_at: row.latest_received_at,
  };
}

function threadFromRow(row: GmailThreadRow): ThreadReaderResponse {
  return {
    entity_id: row.thread_id,
    user_id: DEMO_USER_ID,
    source: 'gmail',
    gmail_thread_id: row.thread_id,
    subject: row.title ?? row.latest_subject,
    total_messages: row.lifecycle_updates.length,
    limit: 50,
    offset: 0,
    has_more: false,
    messages: row.lifecycle_updates.map((update) => ({
      id: update.source_record_id,
      source: 'gmail',
      thread_id: row.thread_id,
      from_address: update.sender ?? row.sender,
      to: 'demo@example.test',
      cc: null,
      bcc: null,
      subject: update.subject,
      body: update.summary ?? row.summary ?? 'Demo message body.',
      html_body: null,
      html_render_document: null,
      snippet: update.summary ?? row.snippet ?? null,
      label_ids: row.label_ids ?? ['INBOX'],
      received_at: update.received_at,
    })),
  };
}

function senderName(sender: string): string {
  const display = sender.split('<', 1)[0]?.trim();
  return display || sender;
}
