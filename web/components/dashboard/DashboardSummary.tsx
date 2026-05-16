'use client';

/** Render the one-line natural-language dashboard summary. */
import React, { useEffect, useMemo, useState } from 'react';
import type { DashboardSummaryData } from './types';

type DashboardSummaryProps = {
  summary: DashboardSummaryData;
};

export function DashboardSummary({ summary }: DashboardSummaryProps) {
  const initialCounts = useMemo(() => countsFromSummary(summary), [summary]);
  const [counts, setCounts] = useState(initialCounts);

  useEffect(() => {
    setCounts(initialCounts);
  }, [initialCounts]);

  useEffect(() => {
    function handleCompleted(event: Event) {
      const detail = (event as CustomEvent<{ entityId?: string; primaryAction?: string; needType?: string; source?: string }>).detail;
      setCounts((current) => {
        const next = { ...current };
        if (detail.entityId && summary.important?.mailGroupId === detail.entityId) {
          next.important = Math.max(0, next.important - 1);
          return next;
        }
        if (detail.source === 'gmail' && detail.primaryAction === 'reply') {
          next.emails = Math.max(0, next.emails - 1);
          return next;
        }
        if (detail.source === 'gmail' && detail.needType === 'decision') {
          next.tasks = Math.max(0, next.tasks - 1);
        }
        return next;
      });
    }

    window.addEventListener('dashboard:item-completed', handleCompleted);
    return () => window.removeEventListener('dashboard:item-completed', handleCompleted);
  }, [summary.important?.mailGroupId]);

  if (summary.parts && summary.parts.length > 0) {
    return (
      <p className="digest-summary">
        <span className="digest-summary-medium">{summary.headline}</span>{' '}
        <span className="digest-summary-light">
          You have {renderPart(summary.parts[0], counts.meetings)}
          {summary.parts[1] ? <>, {renderPart(summary.parts[1], counts.tasks)}</> : null}
          {summary.parts[2] ? <> and {renderPart(summary.parts[2], counts.emails)}</> : null}.
          {summary.important && counts.important > 0 ? (
            <> One important thing: {summary.important.emoji} {summary.important.text}.</>
          ) : null}
          {summary.calendarAvailability ? <> You are {summary.calendarAvailability.text}.</> : null}
        </span>
      </p>
    );
  }

  return (
    <p className="digest-summary">
      <span className="digest-summary-medium">{summary.headline}</span>{' '}
      <span className="digest-summary-light">{summary.brief}</span>
    </p>
  );
}

function countsFromSummary(summary: DashboardSummaryData) {
  return {
    meetings: countForPart(summary, 'meetings'),
    tasks: countForPart(summary, 'tasks'),
    emails: countForPart(summary, 'emails'),
    important: summary.important?.count ?? 0,
  };
}

function countForPart(summary: DashboardSummaryData, type: string): number {
  return summary.parts?.find((part) => part.type === type)?.count ?? 0;
}

function renderPart(part: NonNullable<DashboardSummaryData['parts']>[number], count: number) {
  const text = singularizePartText(part.text, count);
  return (
    <>
      {part.emoji} {count === 0 ? `no ${part.text}` : `${count} ${text}`}
    </>
  );
}

function singularizePartText(text: string, count: number) {
  if (count !== 1) {
    return text;
  }
  if (text === 'emails to reply') {
    return 'email to reply';
  }
  return text.endsWith('s') ? text.slice(0, -1) : text;
}
