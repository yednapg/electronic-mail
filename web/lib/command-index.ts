import type { DashboardResponse, FeedItem, FeedResponse, GmailThreadRow, GmailViewResponse } from './types';

export type CommandKind = 'navigation' | 'work' | 'email' | 'action';

export type CommandItem = {
  id: string;
  kind: CommandKind;
  title: string;
  subtitle: string;
  keywords: string[];
  searchText: string;
  priority: number;
  href?: string;
  entityId?: string;
};

export type CommandIndexResponse = {
  generatedAt: string;
  commands: CommandItem[];
};

const DEFAULT_RESULT_LIMIT = 8;
const STATIC_COMMANDS: Array<Omit<CommandItem, 'searchText'>> = [
  {
    id: 'nav:inbox-home',
    kind: 'navigation',
    title: 'Inbox',
    subtitle: 'Open Gmail-style thread list',
    keywords: ['home', 'gmail', 'mail', 'inbox', 'threads'],
    priority: 20,
    href: '/gmail',
  },
  {
    id: 'nav:gmail',
    kind: 'navigation',
    title: 'Mail',
    subtitle: 'Open Gmail-style thread list',
    keywords: ['gmail', 'mail', 'inbox', 'threads'],
    priority: 24,
    href: '/gmail',
  },
];

const SECTION_CONFIGS = [
  { key: 'now', title: 'Now', priority: 0 },
  { key: 'today', title: 'Today', priority: 6 },
  { key: 'worth_knowing', title: 'Worth Knowing', priority: 12 },
] as const;

export function buildCommandIndex(
  dashboard: DashboardResponse | null,
  generatedAt: Date | string = new Date(),
  gmail: GmailViewResponse | null = null,
): CommandIndexResponse {
  const commands = [...getStaticCommands(), ...buildDashboardCommands(dashboard), ...buildGmailCommands(gmail)];
  return {
    generatedAt: typeof generatedAt === 'string' ? generatedAt : generatedAt.toISOString(),
    commands,
  };
}

export function getStaticCommands(): CommandItem[] {
  return STATIC_COMMANDS.map(withSearchText);
}

export function buildDashboardCommands(dashboard: DashboardResponse | null): CommandItem[] {
  if (dashboard === null || !dashboard.auth.connected) {
    return [];
  }

  const commands: CommandItem[] = [];
  for (const section of SECTION_CONFIGS) {
    const items = dashboard.feed[section.key];
    items.forEach((item, index) => {
      const workCommand = toWorkCommand(item, section.title, section.priority + index);
      if (workCommand !== null) {
        commands.push(workCommand);
      }
    });
  }

  return commands;
}

export function buildGmailCommands(gmail: GmailViewResponse | null): CommandItem[] {
  if (gmail === null) {
    return [];
  }

  const commands: CommandItem[] = [];
  const indexedThreadIds = new Set<string>();

  gmail.sections.forEach((section, sectionIndex) => {
    section.rows.forEach((row, rowIndex) => {
      const command = toGmailThreadCommand(row, section.title, 18 + sectionIndex * 20 + rowIndex, indexedThreadIds);
      if (command !== null) {
        commands.push(command);
      }
    });
  });

  return commands;
}

export function filterCommands(
  commands: CommandItem[],
  query: string,
  limit: number = DEFAULT_RESULT_LIMIT,
): CommandItem[] {
  const normalizedQuery = normalizeSearchText(query);

  if (normalizedQuery.length === 0) {
    return commands
      .filter((command) => command.kind === 'navigation')
      .sort(compareCommands)
      .slice(0, limit);
  }

  const tokens = normalizedQuery.split(' ').filter(Boolean);
  return commands
    .map((command) => ({ command, score: scoreCommand(command, normalizedQuery, tokens) }))
    .filter((result) => result.score > 0)
    .sort((left, right) => {
      if (left.score !== right.score) {
        return right.score - left.score;
      }
      return compareCommands(left.command, right.command);
    })
    .slice(0, limit)
    .map((result) => result.command);
}

export function normalizeSearchText(value: string): string {
  return value
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function toWorkCommand(
  item: FeedItem,
  sectionTitle: string,
  priority: number,
): CommandItem | null {
  if (!shouldIndexItem(item)) {
    return null;
  }

  const title = titleForItem(item);
  const entityId = cleanOptionalText(item.entity_id);
  const gmailThreadId = cleanOptionalText(item.gmail_thread_id);
  const href = gmailThreadId
    ? `/gmail/threads/${encodeURIComponent(gmailThreadId)}`
    : entityId
      ? `/gmail/threads/${encodeURIComponent(entityId)}`
      : '/gmail';

  return withSearchText({
    id: `work:${item.id}`,
    kind: 'work',
    title,
    subtitle: entityId ? `${sectionTitle} - Open related thread` : `${sectionTitle} - Open inbox`,
    keywords: keywordsForItem(item, sectionTitle),
    priority,
    href,
    entityId: entityId || undefined,
  });
}

function toGmailThreadCommand(
  row: GmailThreadRow,
  sectionTitle: string,
  priority: number,
  indexedThreadIds: Set<string>,
): CommandItem | null {
  const entityId = cleanOptionalText(row.entity_id);
  const threadId = cleanOptionalText(row.thread_id);
  if (!threadId || indexedThreadIds.has(threadId)) {
    return null;
  }

  indexedThreadIds.add(threadId);
  const subject = compactText(row.latest_subject || row.summary || row.snippet || 'Untitled email', 92);
  const sender = compactText(formatSender(row.latest_sender), 56);
  const preview = compactText(row.summary || row.snippet || '', 120);

  return withSearchText({
    id: `email:${threadId}`,
    kind: 'email',
    title: `${sender}: ${subject}`,
    subtitle: preview ? `${sectionTitle} - ${preview}` : `${sectionTitle} - Open email`,
    keywords: [
      'gmail',
      'mail',
      'inbox',
      'email',
      sectionTitle,
      threadId,
      row.latest_subject ?? '',
      row.latest_sender ?? '',
      row.summary ?? '',
      row.snippet ?? '',
      ...row.participants,
    ],
    priority,
    href: `/gmail/threads/${encodeURIComponent(threadId)}`,
    entityId: entityId || undefined,
  });
}

function shouldIndexItem(item: FeedItem): boolean {
  return item.source !== 'calendar';
}

function canCompleteItem(item: FeedItem): boolean {
  if (!shouldIndexItem(item)) {
    return false;
  }

  if (item.need_type !== 'decision') {
    return false;
  }

  if (item.current_state === 'done' || item.lifecycle_state === 'resolved' || item.lifecycle_state === 'suppressed') {
    return false;
  }

  return true;
}

function titleForItem(item: FeedItem): string {
  return compactText(item.title || item.why_this_is_here || 'Untitled work item', 96);
}

function keywordsForItem(item: FeedItem, sectionTitle: string): string[] {
  return [
    sectionTitle,
    item.primary_action,
    item.source ?? '',
    item.why_this_is_here,
    item.gmail_thread_id ?? '',
    item.entity_id,
    item.due_at ?? '',
  ].filter((value) => value.trim().length > 0);
}

function withSearchText(command: Omit<CommandItem, 'searchText'>): CommandItem {
  const searchText = normalizeSearchText([command.title, command.subtitle, ...command.keywords].join(' '));
  return { ...command, searchText };
}

function scoreCommand(command: CommandItem, normalizedQuery: string, tokens: string[]): number {
  if (!tokens.every((token) => command.searchText.includes(token))) {
    return 0;
  }

  const title = normalizeSearchText(command.title);
  const subtitle = normalizeSearchText(command.subtitle);
  let score = 20;

  if (title === normalizedQuery) {
    score += 120;
  } else if (title.startsWith(normalizedQuery)) {
    score += 100;
  } else if (title.includes(normalizedQuery)) {
    score += 70;
  }

  if (subtitle.includes(normalizedQuery)) {
    score += 18;
  }

  for (const token of tokens) {
    if (title.startsWith(token)) {
      score += 16;
    } else if (title.includes(token)) {
      score += 10;
    }
  }

  if (command.kind === 'action') {
    score += normalizedQuery.includes('done') || normalizedQuery.includes('complete') ? 35 : 0;
  }

  return score - Math.min(command.priority, 60) / 10;
}

function compareCommands(left: CommandItem, right: CommandItem): number {
  return left.priority - right.priority || left.title.localeCompare(right.title) || left.id.localeCompare(right.id);
}

function cleanOptionalText(value: string | null | undefined): string {
  return typeof value === 'string' ? value.trim() : '';
}

function compactText(value: string, maxLength: number): string {
  const normalized = value.trim().replace(/\s+/g, ' ');
  if (normalized.length <= maxLength) {
    return normalized;
  }
  return `${normalized.slice(0, maxLength - 1)}...`;
}

function formatSender(value: string | null | undefined): string {
  const sender = cleanOptionalText(value);
  if (!sender) {
    return 'Unknown sender';
  }

  const match = sender.match(/^"?([^"<]+?)"?\s*<([^>]+)>$/);
  if (match?.[1]) {
    return match[1].trim();
  }

  return sender;
}

export function flattenFeed(feed: FeedResponse): FeedItem[] {
  return [...feed.now, ...feed.today, ...feed.worth_knowing];
}
