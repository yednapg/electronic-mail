import {
  formatScheduleTime,
  hasExplicitTime,
  isTimedIsoTimestamp,
  toAgendaSortValue,
} from './formatting';
import type { FeedItem, FeedResponse, TimingBand } from './types';
import type {
  DashboardAgendaItem,
  DashboardSectionData,
  DashboardSectionItem,
  DashboardSummaryData,
} from '../components/dashboard/types';

export function buildSummary(dashboard: {
  briefing?: {
    headline: string;
    brief: string;
    parts?: ReadonlyArray<{ type: string; emoji: string; count: number; text: string }>;
    important?: {
      emoji: string;
      count: number;
      text: string;
      mail_group_id?: string | null;
      action_type?: string | null;
    } | null;
    calendar_availability?: {
      emoji: string;
      kind: string;
      time?: string | null;
      text: string;
    } | null;
  } | null;
}): DashboardSummaryData {
  if (dashboard.briefing !== undefined && dashboard.briefing !== null) {
    return {
      headline: dashboard.briefing.headline,
      brief: dashboard.briefing.brief,
      parts: dashboard.briefing.parts?.map((part) => ({ ...part })),
      important: dashboard.briefing.important
        ? {
            emoji: dashboard.briefing.important.emoji,
            count: dashboard.briefing.important.count,
            text: dashboard.briefing.important.text,
            mailGroupId: dashboard.briefing.important.mail_group_id,
            actionType: dashboard.briefing.important.action_type,
          }
        : null,
      calendarAvailability: dashboard.briefing.calendar_availability
        ? {
            emoji: dashboard.briefing.calendar_availability.emoji,
            kind: dashboard.briefing.calendar_availability.kind,
            time: dashboard.briefing.calendar_availability.time,
            text: dashboard.briefing.calendar_availability.text,
          }
        : null,
    };
  }

  return {
    headline: 'Your dashboard is ready.',
    brief: 'Connect Google to generate a personalized briefing.',
  };
}

export function buildSections(feed: FeedResponse): DashboardSectionData[] {
  return [
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
}

function shouldRenderSectionItem(item: FeedItem): boolean {
  return item.source !== 'calendar';
}

export function toSectionItem(item: FeedItem): DashboardSectionItem {
  if (item.need_type === 'awareness') {
    return {
      id: item.id,
      entityId: item.entity_id,
      title:
        item.source === 'calendar' && item.why_this_is_here.trim().length > 0
          ? item.why_this_is_here
          : item.title,
      primaryAction: item.primary_action,
      needType: item.need_type,
      source: item.source ?? undefined,
      detail: toSectionDetail(item),
      cta: toSectionCta(item),
    };
  }

  return {
    id: item.id,
    entityId: item.entity_id,
    title: item.title,
    primaryAction: item.primary_action,
    needType: item.need_type,
    source: item.source ?? undefined,
    detail: toSectionDetail(item),
    cta: toSectionCta(item),
  };
}

export function buildAgenda(feed: FeedResponse): DashboardAgendaItem[] {
  const visibleItems = [...feed.now, ...feed.today, ...feed.worth_knowing];

  return visibleItems
    .flatMap((item) => {
      if (item.source !== 'calendar' || item.due_at === undefined || item.due_at === null) {
        return [];
      }

      const isTimed = isTimedIsoTimestamp(item.due_at);

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
  if (timingBand === 'now') {
    return 'blue';
  }

  if (timingBand === 'today') {
    return 'teal';
  }

  return index % 2 === 0 ? 'green' : 'lime';
}

export function toActionSentence(item: FeedItem): string {
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
  const normalizedTitle = title.toLowerCase();

  return verbs.some((verb) => normalizedTitle.startsWith(verb));
}

export function startsWithActionVerb(title: string): boolean {
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
    const actionUrl = item.detail.action_url ?? undefined;
    return withDetailLinks(item, {
      body: item.detail.body.length > 0 ? [...item.detail.body] : [toDetailDescription(item)],
      actionLabel: item.detail.action_label,
      ...(actionUrl ? { actionUrl } : {}),
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

function withDetailLinks(
  item: FeedItem,
  detail: NonNullable<DashboardSectionItem['detail']>,
): NonNullable<DashboardSectionItem['detail']> {
  const entityId = encodeURIComponent(item.entity_id);

  return {
    ...detail,
    links: {
      threadHref: `/gmail/threads/${entityId}`,
    },
  };
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
  if (item.due_at === null || item.due_at === undefined || item.due_at.length === 0) {
    return null;
  }

  const isTimed = isTimedIsoTimestamp(item.due_at);

  if (!isTimed && hasExplicitTime(item.due_at)) {
    return null;
  }

  return toAgendaSortValue(item.due_at, isTimed);
}
