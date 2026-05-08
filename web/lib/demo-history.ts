import type { HistoryResponse } from './types';

export const demoHistory: HistoryResponse = {
  total: 15,
  limit: 60,
  offset: 0,
  years: [
    {
      year: '2026',
      months: [
        {
          month: '2026-05',
          days: [
            {
              date: '2026-05-05',
              rows: [
                {
                  source_record_id: 'gmail:pycon-us-invite-2026',
                  entity_id: 'pycon-us-invite',
                  source: 'gmail',
                  thread_id: 'pycon-us-2026',
                  received_at: '2026-05-05T13:21:00+05:30',
                  subject: 'PyCon US invitation letter',
                  title: 'PyCon US issued your invitation letter after you shared your passport details',
                  sender: 'PyCon US',
                  snippet: 'Invitation letter received',
                  current_state: 'done',
                  lifecycle_state: 'resolved',
                  outcome_type: 'complete',
                },
                {
                  source_record_id: 'manual:dashboard-opened',
                  entity_id: 'dashboard-setup',
                  source: 'manual',
                  thread_id: 'dashboard-setup',
                  received_at: '2026-05-05T20:00:00+05:30',
                  subject: 'Dashboard opened',
                  title: 'Opened dashboard after Gmail preparation finished',
                  sender: 'Decision Pipeline',
                  snippet: 'Local app event',
                  current_state: 'done',
                  lifecycle_state: 'resolved',
                  outcome_type: 'complete',
                },
              ],
            },
            {
              date: '2026-05-04',
              rows: [
                {
                  source_record_id: 'gmail:flipkart-promo-2026',
                  entity_id: 'flipkart-unsubscribe',
                  source: 'gmail',
                  thread_id: 'flipkart-privacy',
                  received_at: '2026-05-04T09:10:00+05:30',
                  subject: 'Promotional email after unsubscribe',
                  title: 'Flipkart sent another promo after your unsubscribe complaint',
                  sender: 'Flipkart',
                  snippet: 'Unsubscribe thread still noisy',
                  current_state: 'open',
                  lifecycle_state: 'active',
                  outcome_type: null,
                },
              ],
            },
          ],
        },
        {
          month: '2026-04',
          days: [
            {
              date: '2026-04-24',
              rows: [
                {
                  source_record_id: 'gmail:flipkart-privacy-response',
                  entity_id: 'flipkart-unsubscribe',
                  source: 'gmail',
                  thread_id: 'flipkart-privacy',
                  received_at: '2026-04-24T18:40:00+05:30',
                  subject: 'Privacy complaint update',
                  title: 'Flipkart privacy team replied to your unsubscribe complaint',
                  sender: 'Flipkart Privacy',
                  snippet: 'They asked for more details',
                  current_state: 'open',
                  lifecycle_state: 'active',
                  outcome_type: null,
                },
              ],
            },
            {
              date: '2026-04-12',
              rows: [
                {
                  source_record_id: 'gmail:hsbc-account-response',
                  entity_id: 'hsbc-account-query',
                  source: 'gmail',
                  thread_id: 'hsbc-savings-query',
                  received_at: '2026-04-12T11:05:00+05:30',
                  subject: 'Savings account query',
                  title: 'HSBC replied after you corrected the savings account number',
                  sender: 'HSBC',
                  snippet: 'Bank follow-up received',
                  current_state: 'waiting',
                  lifecycle_state: 'active',
                  outcome_type: 'snooze',
                },
              ],
            },
          ],
        },
      ],
    },
    {
      year: '2025',
      months: [
        {
          month: '2025-11',
          days: [
            {
              date: '2025-11-19',
              rows: [
                {
                  source_record_id: 'gmail:apple-delivered',
                  entity_id: 'apple-iphone-order',
                  source: 'gmail',
                  thread_id: 'apple-iphone-order',
                  received_at: '2025-11-19T17:26:00+05:30',
                  subject: 'iPhone delivered',
                  title: 'Apple marked the iPhone order delivered after the shipping updates',
                  sender: 'Apple',
                  snippet: 'Order lifecycle completed',
                  current_state: 'done',
                  lifecycle_state: 'resolved',
                  outcome_type: 'complete',
                },
              ],
            },
          ],
        },
      ],
    },
    {
      year: '2024',
      months: [
        {
          month: '2024-12',
          days: [
            {
              date: '2024-12-19',
              rows: [
                {
                  source_record_id: 'gmail:pycon-au-joss',
                  entity_id: 'pycon-au-joss-paper',
                  source: 'gmail',
                  thread_id: 'pycon-au-joss',
                  received_at: '2024-12-19T21:18:00+05:30',
                  subject: 'JOSS paper submission',
                  title: 'PyCon AU paper moved toward JOSS submission',
                  sender: 'PyCon AU',
                  snippet: 'Paper status changed',
                  current_state: 'open',
                  lifecycle_state: 'active',
                  outcome_type: null,
                },
              ],
            },
          ],
        },
      ],
    },
    {
      year: '2023',
      months: [
        {
          month: '2023-04',
          days: [
            {
              date: '2023-04-23',
              rows: [
                {
                  source_record_id: 'gmail:pycon-us-invitation-issued',
                  entity_id: 'pycon-us-invite',
                  source: 'gmail',
                  thread_id: 'pycon-us-2023',
                  received_at: '2023-04-23T08:36:00+05:30',
                  subject: 'Invitation letter issued',
                  title: 'PyCon US invitation letter issued',
                  sender: 'PyCon US',
                  snippet: 'Conference paperwork started',
                  current_state: 'done',
                  lifecycle_state: 'resolved',
                  outcome_type: 'complete',
                },
              ],
            },
          ],
        },
      ],
    },
  ],
};
