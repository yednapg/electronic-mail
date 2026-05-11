/**
 * Server-rendered dashboard page that turns the feed response into UI view models.
 */
import { getDashboard, isDemoMode } from '../../lib/api';
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

  return sections.filter((section) => section.items.length > 0);
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

  const demoDetail = DEMO_DETAIL_BY_ITEM_ID[item.id];

  if (demoDetail !== undefined) {
    return withDetailLinks(item, demoDetail);
  }

  if (item.primary_action === 'confirm' && /YC Startup School India/i.test(item.title)) {
    return withDetailLinks(item, {
      facts: toDetailFacts(item, 'Waiting on you', 'RSVP before the attendee list closes'),
      body: [
        item.why_this_is_here.trim() || 'YC has accepted your application to attend Startup School India.',
        'The talk is in Bangalore. Only confirm if you can attend.',
        'YC will send a calendar invite after you RSVP.',
      ],
      evidence: ['YC acceptance email', 'Follow-up RSVP reminder', 'Event details from the same thread'],
      confirmLabel: 'Yes, I can attend',
      dismissLabel: 'No',
      sourceLabel: 'Sources: 3 emails from YC',
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

const DEMO_DETAIL_BY_ITEM_ID: Record<string, NonNullable<DashboardSectionItem['detail']>> = {
  'rsvp-yc': {
    facts: [
      { label: 'Current', value: 'Waiting on you' },
      { label: 'Due', value: '11:00 AM' },
      { label: 'Next', value: 'RSVP before the list closes' },
    ],
    body: [
      'YC has accepted your application to attend Startup School India.',
      'The talk is in Bangalore. Only confirm if you can attend. YC will send you a confirmation email with a calendar event once you RSVP.',
    ],
    evidence: ['Acceptance email from YC', 'Reminder from startupschool@ycombinator.com', 'Event details in the same Gmail thread'],
    confirmLabel: 'Yes, I can attend',
    dismissLabel: 'No',
    sourceLabel: 'Source: 3 emails from YC',
  },
  'hdfc-card-bill': {
    facts: [
      { label: 'Current', value: 'Autopay is off' },
      { label: 'Due', value: '5:00 PM today' },
      { label: 'Next', value: 'Pay before 5 PM' },
    ],
    body: [
      'The latest HDFC statement says autopay is not enabled for this card.',
      'A reminder arrived this morning and the due amount is still marked unpaid.',
      'Next move: pay the card before the 5 PM cutoff, then archive the reminder thread.',
    ],
    evidence: ['HDFC statement email', 'Payment reminder from alerts@hdfcbank.net', 'No matching payment receipt found today'],
    confirmLabel: 'Paid',
    dismissLabel: 'Snooze',
    sourceLabel: 'Sources: HDFC statement + reminder emails',
  },
  'github-pr-418': {
    facts: [
      { label: 'Current', value: 'Release waiting on review' },
      { label: 'Due', value: '2:00 PM deploy window' },
      { label: 'Next', value: 'Review diff and reply to Rahul' },
    ],
    body: [
      'Rahul asked for one review before cutting the release branch.',
      'The GitHub notification and Slack-forwarded email both point at the same PR.',
      'Next move: review PR #418, leave a decision, and tell Rahul whether the deploy is unblocked.',
    ],
    evidence: ['GitHub notification for PR #418', 'Rahul follow-up email', 'Release calendar mention at 2 PM'],
    confirmLabel: 'Reviewed',
    dismissLabel: 'Later',
    sourceLabel: 'Sources: GitHub + Rahul thread',
  },
  'airbnb-refund': {
    facts: [
      { label: 'Current', value: 'Refund case can reopen' },
      { label: 'Due', value: 'Before 4:00 PM' },
      { label: 'Next', value: 'Send screenshot' },
    ],
    body: [
      'Airbnb support says they can reopen the refund if you send the payment screenshot.',
      'They asked twice, and the latest message is still unreplied.',
      'Next move: attach the screenshot and keep the case number in the reply.',
    ],
    evidence: ['Airbnb support reply', 'Case #AB-48820', 'Older refund approval email'],
    confirmLabel: 'Replied',
    dismissLabel: 'Skip',
    sourceLabel: 'Sources: 3 Airbnb support emails',
  },
  'pycon-ticket': {
    facts: [
      { label: 'Current', value: 'Free ticket available' },
      { label: 'Due', value: 'No hard deadline' },
      { label: 'Next', value: 'Register if useful' },
    ],
    body: [
      'PyCon DE & PyData sent a free remote ticket offer.',
      'The offer is useful but not urgent, so it stays below the deadline work.',
      'Next move: register only if you want the remote access link.',
    ],
    evidence: ['Offer email from PyCon DE & PyData', 'Registration link in the same thread'],
    confirmLabel: 'Registered',
    dismissLabel: 'Ignore',
    sourceLabel: 'Sources: PyCon offer email',
  },
  'nse-notice': {
    facts: [
      { label: 'Current', value: 'Corporate action notice' },
      { label: 'Due', value: 'Read today' },
      { label: 'Next', value: 'Open notice before acting' },
    ],
    body: [
      'NSE sent an OFS notice about selling shares in the IPO.',
      'The language is financial and needs reading before any decision.',
      'Next move: read the notice and decide whether to act or archive it.',
    ],
    evidence: ['NSE notice email', 'Broker forwarded the same OFS details'],
    confirmLabel: 'Read',
    dismissLabel: 'Archive',
    sourceLabel: 'Sources: NSE + broker emails',
  },
  'mercury-form': {
    facts: [
      { label: 'Current', value: 'PDF ready to send' },
      { label: 'Due', value: '6:00 PM' },
      { label: 'Next', value: 'Send signed form' },
    ],
    body: [
      'Mercury sent the final W-8BEN-E and the thread has the PDF attached.',
      'The form blocks the banking setup for the demo account.',
      'Next move: sign the PDF and reply in the same thread.',
    ],
    evidence: ['Mercury onboarding email', 'Attached W-8BEN-E PDF', 'Follow-up asking for same-day return'],
    confirmLabel: 'Sent',
    dismissLabel: 'Later',
    sourceLabel: 'Sources: Mercury onboarding thread',
  },
  'vercel-invite': {
    facts: [
      { label: 'Current', value: 'Invite pending' },
      { label: 'Due', value: 'Before evening demo' },
      { label: 'Next', value: 'Approve Nikhil' },
    ],
    body: [
      'Nikhil requested Vercel access so he can check preview deploys.',
      'The invite is still pending and the demo branch has active changes.',
      'Next move: approve the team invite, then archive the access email.',
    ],
    evidence: ['Vercel team invite email', 'Nikhil access request', 'Preview deploy link in thread'],
    confirmLabel: 'Approved',
    dismissLabel: 'Deny',
    sourceLabel: 'Sources: Vercel + Nikhil thread',
  },
  'apple-replacement': {
    facts: [
      { label: 'Current', value: 'Replacement shipped' },
      { label: 'Due', value: 'Return label expires tomorrow' },
      { label: 'Next', value: 'Track delivery and return old case' },
    ],
    body: [
      'Apple shipped the replacement AirPods case and sent a return label for the old one.',
      'The label expires tomorrow, so this is not urgent yet but should stay visible today.',
      'Next move: track the replacement and keep the return label handy.',
    ],
    evidence: ['Apple shipment email', 'Return label email', 'Support case update'],
    confirmLabel: 'Tracked',
    dismissLabel: 'Hide',
    sourceLabel: 'Sources: Apple order + support emails',
  },
  'vendor-security': {
    facts: [
      { label: 'Current', value: 'Questionnaire waiting' },
      { label: 'Due', value: 'Today' },
      { label: 'Next', value: 'Review before replying' },
    ],
    body: [
      'A founder prospect sent a security questionnaire before approving the pilot.',
      'The thread has the spreadsheet attached and one follow-up asking for timing.',
      'Next move: review the questions and decide what can be answered today.',
    ],
    evidence: ['Prospect email', 'Attached security questionnaire', 'Follow-up asking for ETA'],
    confirmLabel: 'Reviewed',
    dismissLabel: 'Later',
    sourceLabel: 'Sources: prospect thread + attachment',
  },
  'linear-bug': {
    facts: [
      { label: 'Current', value: 'Bug report has a recording' },
      { label: 'Due', value: 'Today' },
      { label: 'Next', value: 'Reply with fix status' },
    ],
    body: [
      'Maya sent a screen recording showing the onboarding loop still happening.',
      'The latest build may already fix it, but she needs a clear answer.',
      'Next move: check the recording, then reply with the fixed build or a repro question.',
    ],
    evidence: ['Maya bug report email', 'Attached screen recording', 'Latest deploy notification'],
    confirmLabel: 'Replied',
    dismissLabel: 'Later',
    sourceLabel: 'Sources: Maya thread + deploy email',
  },
  'samsung-delivered': {
    facts: [
      { label: 'Current', value: 'Delivered at reception' },
      { label: 'Due', value: 'No action' },
      { label: 'Next', value: 'Pick up when free' },
    ],
    body: [
      'Samsung says the monitor was delivered to reception.',
      'This is useful context, but it should not compete with decisions or payments.',
    ],
    evidence: ['Samsung delivery email', 'Courier proof of delivery'],
    confirmLabel: 'Noted',
    dismissLabel: 'Hide',
    sourceLabel: 'Sources: Samsung + courier emails',
  },
  'notion-export': {
    facts: [
      { label: 'Current', value: 'Backup ready' },
      { label: 'Due', value: 'Link expires in 7 days' },
      { label: 'Next', value: 'Download later' },
    ],
    body: [
      'Notion finished exporting the workspace backup.',
      'The link is available for 7 days, so it belongs in context, not in urgent work.',
    ],
    evidence: ['Notion export complete email', 'Workspace backup link'],
    confirmLabel: 'Saved',
    dismissLabel: 'Hide',
    sourceLabel: 'Sources: Notion system email',
  },
  'aws-budget': {
    facts: [
      { label: 'Current', value: 'Budget warning' },
      { label: 'Due', value: 'Watch today' },
      { label: 'Next', value: 'Check spend if it rises again' },
    ],
    body: [
      'AWS says the month is trending 18% above the warning budget.',
      'It is not a blocker yet, but it is useful context before spinning up more services.',
    ],
    evidence: ['AWS budget alert', 'Previous monthly spend email'],
    confirmLabel: 'Checked',
    dismissLabel: 'Hide',
    sourceLabel: 'Sources: AWS budget email',
  },
};

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
