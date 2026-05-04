import type { SourceRecord, TraceRecord, TraceReplayResponse, TraceStage } from '@electronic-mail/types';

import { demoDashboard } from './demo-dashboard';
import type { FeedItem } from './types';

type DemoEmail = {
  from: string;
  subject: string;
  snippet: string;
  body: string;
  receivedAt: string;
};

const DEMO_EMAILS_BY_ITEM_ID: Record<string, DemoEmail[]> = {
  'rsvp-yc': [
    {
      from: 'startupschool@ycombinator.com',
      subject: 'You are accepted for Startup School India',
      snippet: 'Please RSVP so we can finalize the Bangalore attendee list.',
      body: 'Your application was accepted. RSVP by 11 AM and we will send the calendar invite after confirmation.',
      receivedAt: '2023-02-01T07:42:00+05:30',
    },
    {
      from: 'startupschool@ycombinator.com',
      subject: 'Reminder: RSVP for Startup School India',
      snippet: 'The attendee list closes today.',
      body: 'Quick reminder that the Bangalore list closes today. Confirm only if you can attend in person.',
      receivedAt: '2023-02-01T08:31:00+05:30',
    },
  ],
  'northstar-card-bill': [
    {
      from: 'alerts@northstarbank.example',
      subject: 'Account notice due today',
      snippet: 'Autopay is not enabled for this card. Please pay before 5 PM.',
      body: 'Your card statement is due today. Autopay is not enabled. Pay before 5 PM to avoid late fees.',
      receivedAt: '2023-02-01T06:58:00+05:30',
    },
    {
      from: 'cards@northstarbank.example',
      subject: 'Payment reminder for your Northstar credit card',
      snippet: 'We could not find a matching payment receipt for this statement.',
      body: 'Reminder: we have not found a matching payment receipt for the current statement.',
      receivedAt: '2023-02-01T09:12:00+05:30',
    },
  ],
  'github-pr-418': [
    {
      from: 'github-notifications@example.com',
      subject: 'Review requested: PR #418 release fixes',
      snippet: 'Rahul requested your review before the 2 PM deploy window.',
      body: 'Rahul requested your review on PR #418. The release branch is waiting on this decision.',
      receivedAt: '2023-02-01T08:05:00+05:30',
    },
  ],
  'airbnb-refund': [
    {
      from: 'travel-support@example.com',
      subject: 'Refund case AB-48820 can be reopened',
      snippet: 'Please send the payment screenshot so we can reopen the refund case.',
      body: 'We can reopen case AB-48820 if you send the payment screenshot today.',
      receivedAt: '2023-02-01T08:47:00+05:30',
    },
  ],
  'apple-replacement': [
    {
      from: 'apple-noreply@example.com',
      subject: 'Your replacement AirPods case has shipped',
      snippet: 'The replacement is on the way. The old return label expires tomorrow.',
      body: 'Your replacement AirPods case has shipped. Please return the old case before the label expires tomorrow.',
      receivedAt: '2023-02-01T05:36:00+05:30',
    },
  ],
  'mercury-form': [
    {
      from: 'onboarding@mercury.com',
      subject: 'Final W-8BEN-E form attached',
      snippet: 'Please sign and return the attached PDF before end of day.',
      body: 'The final W-8BEN-E PDF is attached. Send it back today so the banking setup can continue.',
      receivedAt: '2023-02-01T08:18:00+05:30',
    },
  ],
  'vercel-invite': [
    {
      from: 'teams@vercel.com',
      subject: 'Nikhil requested access to your Vercel team',
      snippet: 'Approve this invite so he can inspect preview deploys for the demo branch.',
      body: 'Nikhil requested access to preview deploys. Approve the invite before the evening demo.',
      receivedAt: '2023-02-01T09:04:00+05:30',
    },
  ],
};

export function getDemoSourceRecords(itemId?: string): SourceRecord[] {
  const items = getDemoFeedItems().filter((item) => item.source !== 'calendar');
  const visibleItems = itemId === undefined ? items : items.filter((item) => item.id === itemId);

  return visibleItems.flatMap((item) => {
    const emails = DEMO_EMAILS_BY_ITEM_ID[item.id] ?? [toFallbackEmail(item)];
    const threadId = item.gmail_thread_id ?? `demo-thread-${item.id}`;

    return emails.map((email, index) => ({
      id: `source-${item.id}-${index + 1}`,
      user_id: item.user_id,
      source: 'gmail',
      thread_id: threadId,
      raw_payload: {
        from: email.from,
        to: 'demo@example.test',
        subject: email.subject,
        snippet: email.snippet,
        body: email.body,
        detected_item_id: item.id,
        detected_entity_id: item.entity_id,
        detected_action: item.primary_action,
      },
      received_at: email.receivedAt,
    }));
  });
}

export function getDemoTrace(entityId: string): TraceReplayResponse | null {
  const item = getDemoFeedItems().find((candidate) => candidate.entity_id === entityId || candidate.id === entityId);

  if (item === undefined) {
    return null;
  }

  const sourceRecords = getDemoSourceRecords(item.id);
  const traceId = item.trace_id;
  const sourceRecordIds = sourceRecords.map((record) => record.id);
  const stages: Array<{ stage: TraceStage; input: Record<string, unknown>; output: Record<string, unknown> }> = [
    {
      stage: 'ingestion',
      input: { records: sourceRecordIds },
      output: { source: 'gmail', thread_id: item.gmail_thread_id ?? `demo-thread-${item.id}` },
    },
    {
      stage: 'grouping',
      input: { subjects: sourceRecords.map((record) => record.raw_payload.subject) },
      output: { entity_id: item.entity_id, work_object: item.title },
    },
    {
      stage: 'state_derivation',
      input: { latest_email: sourceRecords.at(-1)?.raw_payload.snippet },
      output: { current_state: item.current_state, lifecycle_state: item.lifecycle_state },
    },
    {
      stage: 'decision',
      input: { why_this_is_here: item.why_this_is_here },
      output: { need_type: item.need_type, timing_band: item.timing_band, importance_level: item.importance_level },
    },
    {
      stage: 'action_selection',
      input: { primary_action: item.primary_action },
      output: { next_move: item.fallback_action === 'open' ? item.primary_action : item.fallback_action },
    },
    {
      stage: 'output',
      input: { entity_id: item.entity_id },
      output: { title: item.title, shown_in_demo_dashboard: true },
    },
  ];

  return {
    entity_id: item.entity_id,
    source_record_ids: sourceRecordIds,
    items: stages.map((stage, index): TraceRecord => ({
      id: `trace-${item.id}-${index + 1}`,
      trace_id: traceId,
      entity_id: item.entity_id,
      source_record_id: sourceRecordIds[index] ?? sourceRecordIds[0] ?? null,
      user_id: item.user_id,
      stage: stage.stage,
      input: stage.input,
      output: stage.output,
      created_at: item.created_at,
    })),
  };
}

function getDemoFeedItems(): FeedItem[] {
  return [...demoDashboard.feed.now, ...demoDashboard.feed.today, ...demoDashboard.feed.worth_knowing];
}

function toFallbackEmail(item: FeedItem): DemoEmail {
  return {
    from: 'inbox@example.com',
    subject: item.title,
    snippet: item.why_this_is_here,
    body: item.why_this_is_here,
    receivedAt: item.created_at,
  };
}
