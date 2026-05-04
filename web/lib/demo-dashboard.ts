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
    importance_level: input.timing === 'now' ? 'high' : 'medium',
    lifecycle_state: input.action === 'none' ? 'scheduled' : 'active',
    current_state: input.action === 'none' ? 'waiting' : 'open',
    source: input.source ?? 'gmail',
    gmail_thread_id: input.threadAction ? `demo-thread-${input.id}` : null,
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
    email: 'demo@example.test',
    display_name: 'TestUser',
  },
  briefing: {
    headline: 'Good morning, TestUser.',
    brief:
      'You have 📆 3 meetings, ✅ 2 tasks and 📨 5 emails to reply, you also have a 💸 1 credit card bill payment due today. You’re mostly free after 🌄 4 pm.',
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
        title: 'RSVP for YC Startup School India',
        why: 'YC has accepted your application to attend Startup School India.',
        timing: 'now',
        action: 'confirm',
      }),
      demoItem({
        id: 'pycon-ticket',
        title: 'PyCon DE & PyData 2026 is offering you a free remote ticket',
        why: 'The event is offering a free remote ticket.',
        timing: 'now',
        action: 'register',
      }),
      demoItem({
        id: 'nse-notice',
        title: 'NSE is offering you to sell your shares in OFS in IPO',
        why: 'The notice needs to be read before taking action.',
        timing: 'now',
        action: 'open',
      }),
    ],
    today: [
    ],
    worth_knowing: [
    ],
  },
};
