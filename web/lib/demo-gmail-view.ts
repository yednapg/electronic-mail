import type { GmailViewResponse } from './types';

export const demoGmailView: GmailViewResponse = {
  total_threads: 3,
  sections: [
    {
      id: 'today',
      title: 'Today',
      rows: [
        {
          thread_id: 'pycon-us-2026',
          entity_id: 'pycon-us-invite',
          latest_source_record_id: 'gmail:pycon-us-invite-2026',
          latest_received_at: '2026-05-05T13:21:00+05:30',
          latest_subject: 'PyCon US invitation letter',
          latest_sender: 'PyCon US',
          participants: ['program@pycon.org', 'you@example.com'],
          message_count: 2,
          summary: 'PyCon US issued your invitation letter after you shared your passport details.',
          snippet: 'Invitation letter received',
          current_state: 'done',
          lifecycle_state: 'resolved',
          outcome_type: 'complete',
          lifecycle_updates: [
            {
              source_record_id: 'gmail:pycon-us-passport-2026',
              received_at: '2026-05-05T10:12:00+05:30',
              subject: 'Passport details received',
              sender: 'PyCon US',
              summary: 'PyCon US confirmed your passport details.',
            },
            {
              source_record_id: 'gmail:pycon-us-invite-2026',
              received_at: '2026-05-05T13:21:00+05:30',
              subject: 'PyCon US invitation letter',
              sender: 'PyCon US',
              summary: 'PyCon US issued your invitation letter.',
            },
          ],
        },
      ],
    },
    {
      id: 'yesterday',
      title: 'Yesterday',
      rows: [
        {
          thread_id: 'flipkart-privacy',
          entity_id: 'flipkart-unsubscribe',
          latest_source_record_id: 'gmail:flipkart-promo-2026',
          latest_received_at: '2026-05-04T09:10:00+05:30',
          latest_subject: 'Promotional email after unsubscribe',
          latest_sender: 'Flipkart',
          participants: ['privacy@flipkart.com', 'you@example.com'],
          message_count: 3,
          summary: 'Flipkart sent another promo after your unsubscribe complaint.',
          snippet: 'Unsubscribe thread still noisy',
          current_state: 'open',
          lifecycle_state: 'active',
          outcome_type: null,
          lifecycle_updates: [
            {
              source_record_id: 'gmail:flipkart-unsubscribe-2026',
              received_at: '2026-05-04T08:01:00+05:30',
              subject: 'Unsubscribe request',
              sender: 'you@example.com',
              summary: 'You asked Flipkart to stop promotional emails.',
            },
            {
              source_record_id: 'gmail:flipkart-privacy-2026',
              received_at: '2026-05-04T08:18:00+05:30',
              subject: 'We received your request',
              sender: 'Flipkart',
              summary: 'Flipkart acknowledged the unsubscribe complaint.',
            },
            {
              source_record_id: 'gmail:flipkart-promo-2026',
              received_at: '2026-05-04T09:10:00+05:30',
              subject: 'New sale starts now',
              sender: 'Flipkart',
              summary: 'Flipkart sent another promo after the complaint.',
            },
          ],
        },
      ],
    },
    {
      id: 'month-2026-04',
      title: 'April 2026',
      rows: [
        {
          thread_id: 'hsbc-savings-query',
          entity_id: 'hsbc-account-query',
          latest_source_record_id: 'gmail:hsbc-account-response',
          latest_received_at: '2026-04-12T11:05:00+05:30',
          latest_subject: 'Savings account query',
          latest_sender: 'HSBC',
          participants: ['bank-support@example.com', 'you@example.com'],
          message_count: 1,
          summary: 'HSBC replied after you corrected the savings account number.',
          snippet: 'Bank follow-up received',
          current_state: 'waiting',
          lifecycle_state: 'active',
          outcome_type: 'snooze',
          lifecycle_updates: [
            {
              source_record_id: 'gmail:hsbc-account-response',
              received_at: '2026-04-12T11:05:00+05:30',
              subject: 'Savings account query',
              sender: 'HSBC',
              summary: 'HSBC replied after you corrected the account number.',
            },
          ],
        },
      ],
    },
  ],
};
