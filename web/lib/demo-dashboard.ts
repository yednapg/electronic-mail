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
    email: 'gaurav@example.com',
    display_name: 'Gaurav Pandey',
  },
  briefing: {
    headline: 'Good morning, Gaurav.',
    brief:
      'You have 📆 3 meetings, ✅ 2 tasks, and 📨 5 emails to reply; you also have 💸 1 credit card bill payment due today. You are mostly free after 🌄 4 pm.',
  },
  feed: {
    now: [
      demoItem({
        id: 'standup',
        title: 'Join design partner standup at 10:00.',
        why: 'The morning standup starts soon and has three external attendees.',
        timing: 'now',
        action: 'join',
        source: 'calendar',
        dueAt: '2026-04-25T10:00:00+05:30',
      }),
      demoItem({
        id: 'reply-vc',
        title: 'Reply to Maya about the seed deck follow-up.',
        why: 'Maya asked for the updated deck and metrics before the partner meeting.',
        timing: 'now',
        action: 'reply',
        threadAction: 'archive',
      }),
      demoItem({
        id: 'pay-card',
        title: 'Pay HDFC credit card bill due today.',
        why: 'The statement shows a bill due today, so paying it avoids fees.',
        timing: 'now',
        action: 'pay',
        threadAction: 'archive',
      }),
    ],
    today: [
      demoItem({
        id: 'product-review',
        title: 'Review the onboarding prototype before 14:30.',
        why: 'The design review is scheduled this afternoon and the team is waiting for comments.',
        timing: 'today',
        action: 'review',
        source: 'calendar',
        dueAt: '2026-04-25T14:30:00+05:30',
      }),
      demoItem({
        id: 'board-prep',
        title: 'Send April traction notes to the board thread.',
        why: 'The board update needs the latest active-user and revenue notes.',
        timing: 'today',
        action: 'send',
        threadAction: 'archive',
      }),
      demoItem({
        id: 'investor-call',
        title: 'Prepare for investor check-in at 16:00.',
        why: 'The next calendar block is an investor call with agenda items already in email.',
        timing: 'today',
        action: 'review',
        source: 'calendar',
        dueAt: '2026-04-25T16:00:00+05:30',
      }),
      demoItem({
        id: 'customer-reply',
        title: 'Reply to Acme about SSO rollout timing.',
        why: 'The customer asked for a concrete rollout date before end of day.',
        timing: 'today',
        action: 'reply',
        threadAction: 'archive',
      }),
      demoItem({
        id: 'legal-reply',
        title: 'Reply to counsel on the revised NDA.',
        why: 'Legal sent a final question that is blocking signature.',
        timing: 'today',
        action: 'reply',
        threadAction: 'archive',
      }),
    ],
    worth_knowing: [
      demoItem({
        id: 'invoice-paid',
        title: 'Stripe confirmed the enterprise invoice was paid.',
        why: 'The payment cleared successfully and is useful context for the board update.',
        timing: 'later',
        action: 'none',
      }),
      demoItem({
        id: 'github-security',
        title: 'GitHub security scan completed with no critical alerts.',
        why: 'The nightly scan finished cleanly, so there is no security follow-up right now.',
        timing: 'later',
        action: 'none',
      }),
      demoItem({
        id: 'travel-hold',
        title: 'Air India held the Mumbai flight fare until tonight.',
        why: 'The fare hold expires tonight if the trip becomes necessary.',
        timing: 'later',
        action: 'none',
      }),
    ],
  },
};
