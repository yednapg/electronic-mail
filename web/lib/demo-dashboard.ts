import type { DashboardResponse, FeedItem, TimingBand } from './types';

const DEMO_USER_ID = 'demo-user';
const DEMO_CREATED_AT = '2026-04-25T09:00:00.000Z';

type DemoItemInput = {
  id: string;
  title: string;
  why: string;
  timing: TimingBand;
  action: string;
  source?: 'gmail' | 'calendar';
  dueAt?: string | null;
  importance?: FeedItem['importance_level'];
  lifecycle?: FeedItem['lifecycle_state'];
  currentState?: FeedItem['current_state'];
  threadId?: string | null;
  threadAction?: FeedItem['gmail_thread_action'];
};

function demoItem(input: DemoItemInput): FeedItem {
  return {
    id: input.id,
    entity_id: `entity-${input.id}`,
    user_id: DEMO_USER_ID,
    need_type: input.action === 'none' ? 'awareness' : 'decision',
    action_type: input.action === 'none' ? 'none' : 'external',
    effort_level: 'quick',
    timing_band: input.timing,
    action_confidence: input.action === 'none' ? 'medium' : 'high',
    primary_action: input.action,
    fallback_action: 'open',
    title: input.title,
    why_this_is_here: input.why,
    due_at: input.dueAt ?? null,
    importance_level: input.importance ?? (input.timing === 'now' ? 'high' : 'medium'),
    lifecycle_state: input.lifecycle ?? (input.action === 'none' ? 'scheduled' : 'active'),
    current_state: input.currentState ?? (input.action === 'none' ? 'waiting' : 'open'),
    source: input.source ?? 'gmail',
    gmail_thread_id: input.threadId ?? (input.threadAction ? `demo-thread-${input.id}` : null),
    gmail_thread_action: input.threadAction ?? null,
    trace_id: `trace-${input.id}`,
    created_at: DEMO_CREATED_AT,
  };
}

export const demoDashboard: DashboardResponse = {
  auth: {
    available: true,
    connected: true,
    connect_url: null,
  },
  profile: {
    email: 'gaurav@example.com',
    display_name: 'Gaurav Pandey',
  },
  briefing: {
    headline: 'Good morning, Gaurav.',
    brief:
      'You have 📆 5 meetings, ✅ 8 open tasks and 📨 11 useful emails pulled into work. Your credit card bill and YC RSVP need a decision before noon. You’re mostly free after 🌄 4 pm.',
  },
  feed: {
    now: [
      demoItem({
        id: 'scrum',
        title: 'Scrum meeting with Team',
        why: 'The scrum meeting starts at 10:00.',
        timing: 'now',
        action: 'none',
        source: 'calendar',
        dueAt: '2023-02-01T10:00:00+05:30',
      }),
      demoItem({
        id: 'pair-programming',
        title: 'Pair programming session with Sam',
        why: 'Pair programming with Sam starts at noon.',
        timing: 'now',
        action: 'none',
        source: 'calendar',
        dueAt: '2023-02-01T12:00:00+05:30',
      }),
      demoItem({
        id: 'lunch',
        title: 'Lunch with Sara',
        why: 'Lunch with Sara is at 13:30.',
        timing: 'later',
        action: 'none',
        source: 'calendar',
        dueAt: '2023-02-01T13:30:00+05:30',
      }),
      demoItem({
        id: 'a16z-hours',
        title: 'a16z Office Hours with Ryan',
        why: 'Office hours with Ryan start at 14:45.',
        timing: 'now',
        action: 'none',
        source: 'calendar',
        dueAt: '2023-02-01T14:45:00+05:30',
      }),
      demoItem({
        id: 'engineering-update',
        title: 'Post today’s update on #engineering',
        why: 'The engineering update is due at 15:00.',
        timing: 'later',
        action: 'none',
        source: 'calendar',
        dueAt: '2023-02-01T15:00:00+05:30',
      }),
      demoItem({
        id: 'rsvp-yc',
        title: 'RSVP within 72 hrs to confirm your spot for YC Startup School India',
        why: 'YC accepted your application and needs your RSVP before the Bangalore attendee list closes.',
        timing: 'now',
        action: 'confirm',
        dueAt: '2023-02-01T11:00:00+05:30',
        threadId: 'yc-startup-school-rsvp',
      }),
      demoItem({
        id: 'hdfc-card-bill',
        title: 'HDFC credit card bill due today',
        why: 'The statement says autopay is off and the bill is due by 5 PM.',
        timing: 'now',
        action: 'pay',
        dueAt: '2023-02-01T17:00:00+05:30',
        threadId: 'hdfc-card-statement-feb',
      }),
      demoItem({
        id: 'github-pr-418',
        title: "Review PR #418 before Rahul's release",
        why: 'Rahul asked for review before the 2 PM deploy window.',
        timing: 'now',
        action: 'review',
        dueAt: '2023-02-01T14:00:00+05:30',
        threadId: 'github-pr-418-release',
      }),
      demoItem({
        id: 'airbnb-refund',
        title: 'Reply to Airbnb refund request',
        why: 'Support needs one more screenshot to reopen the refund case.',
        timing: 'now',
        action: 'reply',
        dueAt: '2023-02-01T16:00:00+05:30',
        threadId: 'airbnb-refund-ab-48820',
      }),
      demoItem({
        id: 'pycon-ticket',
        title: 'PyCon DE & PyData 2026 is offering you a free remote ticket',
        why: 'The event is offering a free remote ticket.',
        timing: 'now',
        action: 'register',
        threadId: 'pycon-free-remote-ticket',
      }),
      demoItem({
        id: 'nse-notice',
        title: 'NSE is offering you to sell your shares in OFS in IPO',
        why: 'The notice needs to be read before taking action.',
        timing: 'now',
        action: 'open',
        threadId: 'nse-ofs-ipo-notice',
      }),
    ],
    today: [
      demoItem({
        id: 'mercury-form',
        title: 'Send signed Mercury banking form',
        why: 'Mercury sent the final W-8BEN-E PDF and asked for it before end of day.',
        timing: 'today',
        action: 'send',
        dueAt: '2023-02-01T18:00:00+05:30',
        threadId: 'mercury-w8bene-final',
      }),
      demoItem({
        id: 'vercel-invite',
        title: 'Approve Vercel team invite for Nikhil',
        why: 'Nikhil requested access to preview deploys for the demo branch.',
        timing: 'today',
        action: 'approve',
        dueAt: '2023-02-01T19:00:00+05:30',
        threadId: 'vercel-team-invite-nikhil',
      }),
      demoItem({
        id: 'apple-replacement',
        title: 'Track Apple order replacement',
        why: 'Apple shipped the replacement AirPods case and the old return label expires tomorrow.',
        timing: 'today',
        action: 'track',
        threadId: 'apple-replacement-airpods',
      }),
      demoItem({
        id: 'vendor-security',
        title: 'Review vendor security questionnaire',
        why: 'A founder prospect sent a security questionnaire before approving the pilot.',
        timing: 'today',
        action: 'review',
        threadId: 'vendor-security-pilot',
      }),
      demoItem({
        id: 'linear-bug',
        title: 'Reply to Linear bug report from Maya',
        why: 'Maya attached a screen recording of the onboarding loop and asked if it is fixed.',
        timing: 'today',
        action: 'reply',
        threadId: 'linear-onboarding-loop-maya',
      }),
    ],
    worth_knowing: [
      demoItem({
        id: 'samsung-delivered',
        title: 'Your Samsung order #12304086779 was delivered.',
        why: 'The delivery email confirms the monitor arrived at reception.',
        timing: 'later',
        action: 'none',
        importance: 'low',
        lifecycle: 'resolved',
        currentState: 'done',
        threadId: 'samsung-monitor-delivered',
      }),
      demoItem({
        id: 'notion-export',
        title: 'Notion finished exporting your workspace backup.',
        why: 'The export link is available for 7 days.',
        timing: 'later',
        action: 'none',
        importance: 'low',
        lifecycle: 'resolved',
        currentState: 'done',
        threadId: 'notion-workspace-export',
      }),
      demoItem({
        id: 'aws-budget',
        title: 'AWS says this month is trending 18% above budget.',
        why: 'The budget alert crossed the warning threshold this morning.',
        timing: 'later',
        action: 'none',
        importance: 'low',
        lifecycle: 'active',
        currentState: 'waiting',
        threadId: 'aws-budget-alert-may',
      }),
    ],
  },
};
