import assert from 'node:assert/strict';
import test from 'node:test';

import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { DashboardView } from './DashboardView';
import type { DashboardSectionData } from './types';

test('dashboard controls stay visible when work sections are empty', () => {
  const sections: DashboardSectionData[] = [
    { id: 'now', title: 'Now', items: [], maxVisible: 6, collapsedByDefault: true },
    { id: 'today', title: 'Today', items: [], maxVisible: 5, collapsedByDefault: true },
    { id: 'worth-knowing', title: 'Worth Knowing', items: [], maxVisible: 3, collapsedByDefault: true },
  ];

  const markup = renderToStaticMarkup(
    React.createElement(DashboardView, {
      dateLabel: 'Monday, May 11',
      timeLabel: '10:30',
      liveMeta: false,
      summary: { headline: 'Ready', brief: 'Nothing urgent.' },
      agenda: [],
      sections,
    }),
  );

  assert.match(markup, /Dashboard view settings/);
  assert.match(markup, /Add item to Now/);
  assert.match(markup, /Add item to Today/);
  assert.match(markup, /Add item to Worth Knowing/);
  assert.match(markup, /Nothing here yet/);
});
