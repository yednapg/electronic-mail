/**
 * Server-rendered dashboard page that turns the feed response into UI view models.
 */
import { getDashboard, getLatestDashboardImportJob, isDemoMode } from '../../lib/api';
import {
  formatClockTime,
  formatDate,
  formatScheduleTime,
  hasExplicitTime,
  isTimedIsoTimestamp,
  toAgendaSortValue,
} from '../../lib/formatting';
import { redirect } from 'next/navigation';
import type { FeedItem, FeedResponse, TimingBand } from '../../lib/types';
import { DashboardView } from '../../components/dashboard/DashboardView';
import type {
  DashboardAgendaItem,
  DashboardSectionData,
  DashboardSectionItem,
  DashboardSummaryData,
} from '../../components/dashboard/types';

export default async function DashboardPage() {
  // Build all derived view models once on the server so leaf components stay simple.
  const dashboard = await getDashboard();
  const demoMode = isDemoMode();
  const now = demoMode ? new Date('2023-02-01T09:00:00') : new Date();

  if (!dashboard.auth.connected) {
    redirect('/');
  }

  if (!demoMode) {
    const latestImportJob = await getLatestDashboardImportJob();
    if (latestImportJob?.status !== 'succeeded') {
      redirect('/post-login');
    }
  }

  const agenda = buildAgenda(dashboard.feed);
  const summary = buildSummary(dashboard);
  const sections = buildSections(dashboard.feed);

  return (
    <DashboardView
      dateLabel={formatDate(now)}
      timeLabel={formatClockTime(now)}
      liveMeta={!demoMode}
      summary={summary}
      agenda={agenda}
      sections={sections}
    />
  );
}

export function buildSummary(dashboard: { briefing?: { headline: string; brief: string } | null }): DashboardSummaryData {
  /** Keep the frontend as a thin renderer over backend-generated summary copy. */
  if (dashboard.briefing !== undefined && dashboard.briefing !== null) {
    return {
      headline: dashboard.briefing.headline,
      brief: dashboard.briefing.brief,
    };
  }

  return {
    headline: 'Your dashboard is ready.',
    brief: 'Connect Google to generate a personalized briefing.',
  };
}

export function buildSections(feed: FeedResponse): DashboardSectionData[] {
  /** Keep section assembly separate so tests can validate ordering and copy in isolation. */
  const sections: DashboardSectionData[] = [
    {
      id: 'now',
      title: 'Now',
      items: sortSectionFeedItems(feed.now).filter(shouldRenderSectionItem).map(toSectionItem),
      maxVisible: 6,
      collapsedByDefault: true,
    },
    {
      id: 'today',
      title: 'Today',
      items: sortSectionFeedItems(feed.today).filter(shouldRenderSectionItem).map(toSectionItem),
      maxVisible: 5,
      collapsedByDefault: true,
    },
    {
      id: 'worth-knowing',
      title: 'Worth Knowing',
      items: sortSectionFeedItems(feed.worth_knowing).filter(shouldRenderSectionItem).map(toSectionItem),
      maxVisible: 3,
      collapsedByDefault: true,
    },
  ];

  return sections;
}

function shouldRenderSectionItem(item: FeedItem): boolean {
  return item.source !== 'calendar';
}

export function toSectionItem(item: FeedItem): DashboardSectionItem {
  // Awareness-only calendar items already come with natural sentence copy from the backend.
  if (item.need_type === 'awareness') {
    return {
      id: item.id,
      entityId: item.entity_id,
      title:
        item.source === 'calendar' && item.why_this_is_here.trim().length > 0
          ? item.why_this_is_here
          : item.title,
      detail: toSectionDetail(item),
      cta: toSectionCta(item),
    };
  }

  return {
    id: item.id,
    entityId: item.entity_id,
    title: toActionSentence(item),
    detail: toSectionDetail(item),
    cta: toSectionCta(item),
  };
}

export function buildAgenda(feed: FeedResponse): DashboardAgendaItem[] {
  /** Pull only calendar-backed items into the compact agenda strip. */
  const visibleItems = [...feed.now, ...feed.today, ...feed.worth_knowing];

  return visibleItems
    .flatMap((item) => {
      if (item.source !== 'calendar' || item.due_at === undefined || item.due_at === null) {
        return [];
      }

      const isTimed = isTimedIsoTimestamp(item.due_at);

      // Ignore malformed all-day strings that accidentally include a clock value.
      if (!isTimed && hasExplicitTime(item.due_at)) {
        return [];
      }

      return [
        {
          id: item.id,
          time: isTimed ? formatScheduleTime(item.due_at) : 'All day',
          title: toActionSentence(item),
          allDay: !isTimed,
          sortValue: toAgendaSortValue(item.due_at, isTimed),
          timingBand: item.timing_band,
        },
      ];
    })
    .sort((left, right) => {
      if (left.sortValue !== right.sortValue) {
        return left.sortValue - right.sortValue;
      }

      if (left.allDay !== right.allDay) {
        return left.allDay ? -1 : 1;
      }

      return left.time.localeCompare(right.time) || left.title.localeCompare(right.title);
    })
    .map(({ sortValue: _sortValue, timingBand, ...item }, index) => ({
      ...item,
      tone: toneForTimingBand(timingBand, index),
    }));
}

export function toneForTimingBand(timingBand: TimingBand, index: number): DashboardAgendaItem['tone'] {
  /** Convert urgency into a simple color family for the agenda rail. */
  if (timingBand === 'now') {
    return 'blue';
  }

  if (timingBand === 'today') {
    return 'teal';
  }

  return index % 2 === 0 ? 'green' : 'lime';
}

export function toActionSentence(item: FeedItem): string {
  /** Normalize backend titles into short action-first UI copy. */
  const cleanTitle = item.title.trim();

  if (startsWithActionVerb(cleanTitle) || shouldKeepNaturalTitle(item, cleanTitle)) {
    return cleanTitle;
  }

  switch (item.primary_action) {
    case 'reply':
      if (cleanTitle.toLowerCase() === 'reply needed') {
        return 'Reply to this thread';
      }

      return startsWithVerb(cleanTitle, ['reply', 'respond']) ? cleanTitle : `Reply about ${cleanTitle}`;
    case 'confirm':
      if (cleanTitle.toLowerCase() === 'rsvp needed') {
        return 'Confirm this invite';
      }

      return startsWithVerb(cleanTitle, ['confirm', 'rsvp']) ? cleanTitle : `Confirm ${cleanTitle}`;
    case 'pay':
      return startsWithVerb(cleanTitle, ['pay']) ? cleanTitle : `Pay ${stripDuePrefix(cleanTitle)}`;
    case 'track':
      return startsWithVerb(cleanTitle, ['track']) ? cleanTitle : `Track ${stripDuePrefix(cleanTitle)}`;
    case 'review':
      return `Review ${cleanTitle}`;
    case 'join':
      return `Join ${cleanTitle}`;
    case 'send':
      return `Send ${cleanTitle}`;
    case 'approve':
      return `Approve ${cleanTitle}`;
    case 'register':
      return `Register for ${cleanTitle}`;
    case 'open':
      if (cleanTitle.startsWith('Due ')) {
        return `Review item due ${cleanTitle.slice(4)}`;
      }

      return startsWithVerb(cleanTitle, ['review', 'open']) ? cleanTitle : `Review ${cleanTitle}`;
    case 'none':
    default:
      return cleanTitle;
  }
}

export function shouldKeepNaturalTitle(item: FeedItem, title: string): boolean {
  /** Preserve already-natural status sentences instead of forcing an imperative verb. */
  if (item.need_type === 'awareness') {
    return true;
  }

  if (/^within\b/i.test(title)) {
    return true;
  }

  if (/\bis offering\b/i.test(title)) {
    return true;
  }

  if (/[.!?]$/.test(title)) {
    return true;
  }

  return /^(you\b|your\b|you're\b|we\b|this\b|[A-Z][A-Za-z0-9&.'/-]+(?: [A-Z][A-Za-z0-9&.'/-]+){0,4} (?:updated|declined|approved|confirmed|registered|delivered|shipped|sent|accepted|resolved|says|changed|scheduled|is offering)\b)/i.test(
    title,
  );
}

export function startsWithVerb(title: string, verbs: string[]): boolean {
  /** Small helper used to avoid duplicate action prefixes in UI copy. */
  const normalizedTitle = title.toLowerCase();

  return verbs.some((verb) => normalizedTitle.startsWith(verb));
}

export function startsWithActionVerb(title: string): boolean {
  /** Detect whether a title is already action-first. */
  return startsWithVerb(title, [
    'reply',
    'respond',
    'confirm',
    'rsvp',
    'pay',
    'track',
    'review',
    'open',
    'join',
    'send',
    'approve',
    'register',
  ]);
}

export function stripDuePrefix(title: string): string {
  /** Soften titles like "Due Friday" when converting them into a sentence. */
  return title.startsWith('Due ') ? `item due ${title.slice(4)}` : title;
}

export function toSectionCta(item: FeedItem): DashboardSectionItem['cta'] | undefined {
  if (item.primary_action === 'confirm' && /^within\b/i.test(item.title.trim())) {
    return {
      label: 'RSVP',
      tone: 'blue',
      placement: 'prefix',
    };
  }

  if (item.primary_action === 'register') {
    return {
      label: 'Register!',
      tone: 'blue',
    };
  }

  if (item.title.toLowerCase().includes('read notice') || item.title.toLowerCase().includes('ofs in ipo')) {
    return {
      label: 'Read Notice',
      tone: 'green',
    };
  }

  return undefined;
}

export function toSectionDetail(item: FeedItem): DashboardSectionItem['detail'] | undefined {
  if (item.source !== 'gmail') {
    return undefined;
  }

  if (item.detail !== undefined && item.detail !== null) {
    return withDetailLinks(item, {
      body: item.detail.body.length > 0 ? [...item.detail.body] : [toDetailDescription(item)],
      actionLabel: item.detail.action_label,
      confirmLabel: primaryActionDoneLabel(item.primary_action),
      dismissLabel: 'Not needed',
      sourceLabel: item.detail.source_label,
    });
  }

  return withDetailLinks(item, {
    body: [toDetailDescription(item)],
    actionLabel: toDetailActionLabel(item),
    confirmLabel: primaryActionDoneLabel(item.primary_action),
    dismissLabel: 'Not needed',
    sourceLabel: sourceLabelForItem(item),
  });
}

function toDetailFacts(item: FeedItem, current: string, next: string): NonNullable<DashboardSectionItem['detail']>['facts'] {
  const facts = [
    { label: 'Current', value: current },
    { label: 'Next', value: next },
  ];

  if (item.due_at !== undefined && item.due_at !== null && item.due_at.length > 0) {
    facts.splice(1, 0, { label: 'Due', value: formatScheduleTime(item.due_at) });
  }

  return facts;
}

function withDetailLinks(
  item: FeedItem,
  detail: NonNullable<DashboardSectionItem['detail']>,
): NonNullable<DashboardSectionItem['detail']> {
  const entityId = encodeURIComponent(item.entity_id);

  return {
    ...detail,
    links: {
      threadHref: `/entities/${entityId}/thread`,
    },
  };
}

function toCurrentStateLabel(item: FeedItem): string {
  if (item.current_state === 'waiting') {
    return 'Waiting on someone else';
  }

  if (item.current_state === 'done') {
    return 'Already done';
  }

  return 'Waiting on you';
}

function toNextMoveLabel(item: FeedItem): string {
  if (item.current_state === 'waiting' && (item.primary_action === 'none' || item.primary_action === 'open')) {
    return 'Wait for the reply';
  }

  switch (item.primary_action) {
    case 'reply':
      return 'Reply in the thread';
    case 'confirm':
      return 'Confirm or decline';
    case 'pay':
      return 'Make the payment';
    case 'track':
      return 'Check latest status';
    case 'review':
      return 'Review and decide';
    case 'send':
      return 'Send the missing item';
    case 'approve':
      return 'Approve or deny';
    case 'register':
      return 'Register if useful';
    case 'open':
      return 'Open and read';
    default:
      return 'Read the latest email';
  }
}

function toDetailDescription(item: FeedItem): string {
  const explanation = item.why_this_is_here.trim();
  if (explanation.length > 0) {
    return explanation;
  }

  const title = item.title.trim();
  if (title.length > 0) {
    return `${title} is still part of your mailbox work.`;
  }

  return 'This email needs attention.';
}

function toDetailActionLabel(item: FeedItem): string {
  return toNextMoveLabel(item);
}

function primaryActionDoneLabel(action: string): string {
  switch (action) {
    case 'reply':
      return 'Replied';
    case 'confirm':
      return 'Confirmed';
    case 'pay':
      return 'Paid';
    case 'track':
      return 'Tracked';
    case 'review':
      return 'Reviewed';
    case 'send':
      return 'Sent';
    case 'approve':
      return 'Approved';
    case 'register':
      return 'Registered';
    case 'open':
      return 'Read';
    default:
      return 'Done';
  }
}

function sourceLabelForItem(item: FeedItem): string {
  if (item.source === 'calendar') {
    return 'Calendar';
  }

  return 'Gmail';
}

function sortSectionFeedItems(items: FeedItem[]): FeedItem[] {
  /** Keep section ordering stable: due date first, then calendar items, then title. */
  return [...items].sort((left, right) => {
    const leftDueAt = toFeedSortValue(left);
    const rightDueAt = toFeedSortValue(right);

    if (leftDueAt !== null && rightDueAt !== null && leftDueAt !== rightDueAt) {
      return leftDueAt - rightDueAt;
    }

    if (leftDueAt !== null && rightDueAt === null) {
      return -1;
    }

    if (leftDueAt === null && rightDueAt !== null) {
      return 1;
    }

    if (left.source === 'calendar' && right.source !== 'calendar') {
      return -1;
    }

    if (left.source !== 'calendar' && right.source === 'calendar') {
      return 1;
    }

    return 0;
  });
}

function toFeedSortValue(item: FeedItem): number | null {
  /** Reuse agenda time parsing for feed-section sorting when a due date exists. */
  if (item.due_at === null || item.due_at === undefined || item.due_at.length === 0) {
    return null;
  }

  const isTimed = isTimedIsoTimestamp(item.due_at);

  if (!isTimed && hasExplicitTime(item.due_at)) {
    return null;
  }

  return toAgendaSortValue(item.due_at, isTimed);
}
