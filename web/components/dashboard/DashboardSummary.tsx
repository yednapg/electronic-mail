/** Render the one-line natural-language dashboard summary. */
import React from 'react';
import type { DashboardSummaryData } from './types';

type DashboardSummaryProps = {
  summary: DashboardSummaryData;
};

export function DashboardSummary({ summary }: DashboardSummaryProps) {
  return (
    <p className="digest-summary">
      <span className="digest-summary-medium">{summary.headline}</span>{' '}
      <span className="digest-summary-light">{summary.brief}</span>
    </p>
  );
}
