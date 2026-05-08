import Link from 'next/link';
import React from 'react';
import type { CSSProperties } from 'react';

import { getHistory } from '../../lib/api';
import { formatScheduleTime } from '../../lib/formatting';
import type { HistoryItem, HistoryResponse } from '../../lib/types';

export default async function HistoryPage() {
  const history = await getHistory();

  return <HistoryView history={history} />;
}

export function HistoryView({ history }: { history: HistoryResponse }) {
  return (
    <main className="digest-page">
      <div className="digest-shell history-shell">
        <div className="history-topline">
          <Link href="/dashboard" className="history-back-link">
            Dashboard
          </Link>
          <p className="history-count">{history.total} items</p>
        </div>
        <h1 className="history-page-title">History</h1>

        {history.years.length > 0 ? (
          <div className="history-groups">
            {history.years.map((year) => (
              <section className="history-year" key={year.year} aria-label={`${year.year}`}>
                <div className="digest-section-header history-year-header">
                  <h2 className="digest-section-title">{year.year}</h2>
                  <span className="digest-section-rule" aria-hidden="true" />
                </div>

                <div className="history-months">
                  {year.months.map((month) => (
                    <section
                      className="history-month"
                      key={`${year.year}-${month.month}`}
                      aria-label={`${formatMonthLabel(month.month)} ${year.year}`}
                    >
                      <h3 className="history-month-title">{formatMonthLabel(month.month)}</h3>

                      {month.days.map((day) => (
                        <section className="history-day" key={day.date} aria-label={formatDayLabel(day.date)}>
                          <h4 className="history-day-label">{formatDayLabel(day.date)}</h4>
                          <ul className="history-list">
                            {day.rows.map((item, index) => (
                              <HistoryRow key={item.source_record_id} item={item} index={index} />
                            ))}
                          </ul>
                        </section>
                      ))}
                    </section>
                  ))}
                </div>
              </section>
            ))}
          </div>
        ) : (
          <p className="history-empty">No imported history yet.</p>
        )}
      </div>
    </main>
  );
}

function HistoryRow({ item, index }: { item: HistoryItem; index: number }) {
  const detailId = `history-detail-${item.source_record_id}`;
  const timeLabel = formatScheduleTime(item.received_at);
  const sourceLabel = item.sender ?? item.source;
  const metaParts = [timeLabel, sourceLabel].filter((part) => part.length > 0);
  const markerLabel = item.current_state === 'done' ? 'Completed' : item.current_state === 'waiting' ? 'Waiting' : 'Open';
  const stateClass = item.current_state ?? 'open';
  const title = item.title ?? item.subject ?? 'Untitled item';
  const summary = item.summary ?? item.snippet;

  return (
    <li className="history-item" style={{ '--attention-index': index } as CSSProperties}>
      <span className={`history-marker history-marker-${stateClass}`} aria-label={markerLabel}>
        {item.current_state === 'done' ? '✓' : null}
      </span>
      <details className="history-details">
        <summary className="history-summary" aria-controls={detailId}>
          <span className="history-row-title">{title}</span>
          {metaParts.length > 0 ? <span className="history-row-meta">{metaParts.join(' · ')}</span> : null}
        </summary>
        <div id={detailId} className="history-detail">
          {summary ? <p>{summary}</p> : null}
          <p>{historyStateLabel(item)}</p>
        </div>
      </details>
    </li>
  );
}

function historyStateLabel(item: HistoryItem): string {
  if (item.outcome_type === 'complete' || item.current_state === 'done') {
    return 'Done in app state.';
  }

  if (item.outcome_type === 'snooze') {
    return 'Snoozed in app state.';
  }

  if (item.outcome_type === 'dismiss') {
    return 'Dismissed in app state.';
  }

  if (item.current_state === 'waiting') {
    return 'Waiting for someone else.';
  }

  return 'Still open in app state.';
}

export function formatMonthLabel(month: string): string {
  const date = new Date(`${month}-01T00:00:00`);

  if (Number.isNaN(date.getTime())) {
    return month;
  }

  return date.toLocaleDateString('en-US', { month: 'long' });
}

export function formatDayLabel(dateString: string): string {
  const date = new Date(`${dateString}T00:00:00`);

  if (Number.isNaN(date.getTime())) {
    return dateString;
  }

  const weekday = date.toLocaleDateString('en-US', { weekday: 'short' });
  return `${weekday} ${date.getDate()}`;
}
