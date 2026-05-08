import assert from 'node:assert/strict';
import test from 'node:test';

import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { HistoryView, formatDayLabel, formatMonthLabel } from './page';
import type { HistoryResponse } from '../../lib/types';

test('history date labels stay compact and separate from row alignment', () => {
  assert.equal(formatMonthLabel('2026-05'), 'May');
  assert.equal(formatDayLabel('2026-05-05'), 'Tue 5');
});

test('history view renders grouped rows without row-level Gmail actions', () => {
  const history: HistoryResponse = {
    total: 1,
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
                    source_record_id: 'record-1',
                    entity_id: 'entity-1',
                    source: 'gmail',
                    thread_id: 'thread-1',
                    received_at: '2026-05-05T09:00:00+05:30',
                    title: 'PyCon US issued your invitation letter',
                    sender: 'PyCon US',
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

  const markup = renderToStaticMarkup(React.createElement(HistoryView, { history }));

  assert.match(markup, /History/);
  assert.match(markup, /2026/);
  assert.match(markup, /May/);
  assert.match(markup, /Tue 5/);
  assert.match(markup, /PyCon US issued your invitation letter/);
  assert.doesNotMatch(markup, /Archive|Unarchive/);
});
