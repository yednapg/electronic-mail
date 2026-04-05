/**
 * Server-rendered dashboard page that turns the feed response into UI view models.
 */
import { getFeed } from '../../lib/api';
import {
  formatClockTime,
  formatDate,
  formatScheduleTime,
  hasExplicitTime,
  isTimedIsoTimestamp,
  toAgendaSortValue,
} from '../../lib/formatting';
import type { FeedItem, FeedResponse, PrimaryActionType, TimingBand } from '../../lib/types';
import { DashboardAgenda } from '../../components/dashboard/DashboardAgenda';
import { DashboardMeta } from '../../components/dashboard/DashboardMeta';
import { DashboardSection } from '../../components/dashboard/DashboardSection';
import { DashboardSummary } from '../../components/dashboard/DashboardSummary';
import type {
  DashboardAgendaItem,
  DashboardSectionData,
  DashboardSectionItem,
  DashboardSummaryData,
} from '../../components/dashboard/types';

export default async function DashboardPage() {
  // Build all derived view models once on the server so leaf components stay simple.
  const feed = await getFeed();
  const now = new Date();
  const agenda = buildAgenda(feed);
  const summary = buildSummary(feed, agenda);
  const sections = buildSections(feed);

  return (
    <main className="digest-page">
      <div className="digest-shell">
        <DashboardMeta dateLabel={formatDate(now)} timeLabel={formatClockTime(now)} />
        <DashboardSummary summary={summary} />
        <DashboardAgenda items={agenda} />

        <div className="digest-sections">
          {sections.map((section) => (
            <DashboardSection
              key={section.id}
              title={section.title}
              items={section.items}
              maxVisible={section.maxVisible}
              collapsedByDefault={section.collapsedByDefault}
            />
          ))}
        </div>
      </div>
    </main>
  );
}

export function buildSummary(
  feed: FeedResponse,
  agenda: DashboardAgendaItem[],
): DashboardSummaryData {
  /** Summarize visible work and schedule density for the sentence at the top of the page. */
  const visibleItems = [...feed.now, ...feed.today, ...feed.worth_knowing];
  const replyCount = countByAction(visibleItems, 'reply');
  const paymentCount = countByAction(visibleItems, 'pay');
  const taskCount = visibleItems.filter(
    (item) =>
      item.primary_action === 'open' ||
      item.primary_action === 'track' ||
      item.primary_action === 'confirm' ||
      item.primary_action === 'review' ||
      item.primary_action === 'join' ||
      item.primary_action === 'send' ||
      item.primary_action === 'approve' ||
      item.primary_action === 'register',
  ).length;

  return {
    greeting: getGreetingForTime(new Date()),
    name: 'Gaurav',
    meetingCount: agenda.length,
    taskCount,
    replyCount,
    paymentCount,
    freeAfterLabel: agenda.at(-1)?.time ?? 'the rest of the day',
  };
}

export function buildSections(feed: FeedResponse): DashboardSectionData[] {
  /** Keep section assembly separate so tests can validate ordering and copy in isolation. */
  return [
    {
      id: 'now',
      title: 'Now',
      items: sortSectionFeedItems(feed.now).map(toSectionItem),
      maxVisible: 4,
      collapsedByDefault: true,
    },
    {
      id: 'today',
      title: 'Today',
      items: sortSectionFeedItems(feed.today).map(toSectionItem),
      maxVisible: 5,
      collapsedByDefault: true,
    },
    {
      id: 'worth-knowing',
      title: 'Worth Knowing',
      items: sortSectionFeedItems(feed.worth_knowing).map(toSectionItem),
      maxVisible: 3,
      collapsedByDefault: true,
    },
  ];
}

export function toSectionItem(item: FeedItem): DashboardSectionItem {
  // Awareness-only calendar items already come with natural sentence copy from the backend.
  if (item.source === 'calendar' && item.need_type === 'awareness') {
    return {
      id: item.id,
      title: item.why_this_is_here,
    };
  }

  return {
    id: item.id,
    title: toActionSentence(item),
  };
}

export function countByAction(items: FeedItem[], action: PrimaryActionType): number {
  /** Count a specific primary action across the visible feed. */
  return items.filter((item) => item.primary_action === action).length;
}

export function getGreetingForTime(date: Date): string {
  /** Pick the greeting label from the local hour. */
  const hour = date.getHours();

  if (hour >= 12 && hour < 18) {
    return 'Good afternoon';
  }

  if (hour >= 18) {
    return 'Good evening';
  }

  return 'Good morning';
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

  if (startsWithActionVerb(cleanTitle)) {
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

    return left.title.localeCompare(right.title);
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
