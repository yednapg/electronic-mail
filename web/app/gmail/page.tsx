import Link from 'next/link';
import React from 'react';
import type { CSSProperties } from 'react';

import { getGmailView } from '../../lib/api';
import type { GmailThreadRow, GmailThreadUpdate, GmailViewResponse } from '../../lib/types';

export default async function GmailPage() {
  const gmail = await getGmailView();

  return <GmailView gmail={gmail} />;
}

export function GmailView({ gmail }: { gmail: GmailViewResponse }) {
  return (
    <main className="digest-page">
      <div className="digest-shell gmail-shell">
        <div className="history-topline">
          <Link href="/dashboard" className="history-back-link">
            Dashboard
          </Link>
          <p className="history-count">{gmail.total_threads} threads</p>
        </div>
        <h1 className="history-page-title">Gmail</h1>

        {gmail.sections.length > 0 ? (
          <div className="gmail-groups">
            {gmail.sections.map((section) => (
              <section className="gmail-section" key={section.id} aria-label={section.title}>
                <div className="digest-section-header history-year-header">
                  <h2 className="digest-section-title">{section.title}</h2>
                  <span className="digest-section-rule" aria-hidden="true" />
                </div>
                <ul className="gmail-list">
                  {section.rows.map((row, index) => (
                    <GmailThreadRowItem key={row.thread_id} row={row} index={index} />
                  ))}
                </ul>
              </section>
            ))}
          </div>
        ) : (
          <p className="history-empty">No imported Gmail threads yet.</p>
        )}
      </div>
    </main>
  );
}

function GmailThreadRowItem({ row, index }: { row: GmailThreadRow; index: number }) {
  const title = row.latest_subject?.trim() || 'Untitled thread';
  const summary = row.summary ?? row.snippet;
  const stateLabel = gmailStateLabel(row);
  const detailHref = row.entity_id ? `/entities/${encodeURIComponent(row.entity_id)}/thread` : null;

  return (
    <li className="gmail-thread-row" style={{ '--attention-index': index } as CSSProperties}>
      <div className="gmail-thread-copy">
        <div className="gmail-thread-heading">
          <h3 className="gmail-thread-title">{title}</h3>
          <span className="gmail-thread-time">{formatGmailReceivedAt(row.latest_received_at)}</span>
        </div>
        <p className="gmail-thread-meta">
          {[row.latest_sender, formatMessageCount(row.message_count), stateLabel].filter(Boolean).join(' · ')}
        </p>
        {summary ? <p className="gmail-thread-summary">{summary}</p> : null}
        {row.lifecycle_updates.length > 1 ? <GmailLifecycle updates={row.lifecycle_updates} /> : null}
        {row.participants.length > 0 ? <p className="gmail-thread-participants">{row.participants.join(', ')}</p> : null}
      </div>
      {detailHref ? (
        <Link className="gmail-thread-link" href={detailHref}>
          Open
        </Link>
      ) : null}
    </li>
  );
}

function GmailLifecycle({ updates }: { updates: GmailThreadUpdate[] }) {
  return (
    <ol className="gmail-thread-lifecycle" aria-label="Thread updates">
      {updates.map((update) => (
        <li className="gmail-thread-lifecycle-step" key={update.source_record_id}>
          <span className="gmail-thread-lifecycle-time">{formatGmailReceivedAt(update.received_at)}</span>
          <span className="gmail-thread-lifecycle-copy">{update.summary ?? update.subject ?? 'Email update'}</span>
        </li>
      ))}
    </ol>
  );
}

function formatMessageCount(count: number): string {
  return count === 1 ? '1 email' : `${count} emails`;
}

function gmailStateLabel(row: GmailThreadRow): string {
  if (row.outcome_type === 'complete' || row.current_state === 'done') {
    return 'Done';
  }

  if (row.outcome_type === 'snooze') {
    return 'Snoozed';
  }

  if (row.outcome_type === 'dismiss') {
    return 'Dismissed';
  }

  if (row.current_state === 'waiting') {
    return 'Waiting';
  }

  return 'Open';
}

export function formatGmailReceivedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  }).format(date);
}
