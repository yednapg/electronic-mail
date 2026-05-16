import React from 'react';
import type { CSSProperties } from 'react';

import { SignedInAppChrome } from '../../components/app/AppChrome';
import type { GmailThreadRow, GmailViewResponse } from '../../lib/types';

type GmailListProps = {
  gmail: GmailViewResponse;
  activeThreadId?: string | null;
  onOpenThread?: (row: GmailThreadRow) => void;
  onPrefetchThread?: (threadId: string) => void;
};

export function GmailView({ gmail }: { gmail: GmailViewResponse }) {
  return (
    <SignedInAppChrome active="gmail">
      <GmailList gmail={gmail} />
    </SignedInAppChrome>
  );
}

export function GmailList({
  gmail,
  activeThreadId,
  onOpenThread,
  onPrefetchThread,
}: GmailListProps) {
  return (
    <main className="digest-page">
      <div className="digest-shell gmail-shell gmail-inbox-shell">
        <div className="inbox-topline">
          <p className="inbox-count">{gmail.total_threads} threads</p>
        </div>
        <h1 className="inbox-page-title">Inbox</h1>

        {gmail.sections.length > 0 ? (
          <div className="gmail-groups">
            {gmail.sections.map((section) => (
              <section className="gmail-section" key={section.id} aria-label={section.title}>
                <div className="gmail-section-header">
                  <h2 className="gmail-section-title">{section.title}</h2>
                </div>
                <ul className="gmail-list">
                  {section.rows.map((row, index) => (
                    <GmailThreadRowItem
                      key={row.thread_id}
                      row={row}
                      index={index}
                      isActive={row.thread_id === activeThreadId}
                      onOpenThread={onOpenThread}
                      onPrefetchThread={onPrefetchThread}
                    />
                  ))}
                </ul>
              </section>
            ))}
          </div>
        ) : (
          <p className="inbox-empty">No imported Gmail threads yet.</p>
        )}
      </div>
    </main>
  );
}

function GmailThreadRowItem({
  row,
  index,
  isActive,
  onOpenThread,
  onPrefetchThread,
}: {
  row: GmailThreadRow;
  index: number;
  isActive: boolean;
  onOpenThread?: (row: GmailThreadRow) => void;
  onPrefetchThread?: (threadId: string) => void;
}) {
  const sender = formatGmailSender(row.latest_sender);
  const title = row.title?.trim() || row.latest_subject?.trim() || 'Untitled thread';
  const detailHref = `/gmail/threads/${encodeURIComponent(row.thread_id)}`;
  const rowCopy = (
    <>
      <span className="gmail-thread-sender">{sender}</span>
      <span className="gmail-thread-message">{compactInboxText(title, 120)}</span>
      <span className="gmail-thread-time">{formatGmailReceivedAt(row.latest_received_at)}</span>
    </>
  );

  return (
    <li className="gmail-thread-row" style={{ '--attention-index': index } as CSSProperties}>
      <a
        className={`gmail-thread-link ${isActive ? 'is-active' : ''}`}
        data-gmail-thread-id={row.thread_id}
        href={detailHref}
        aria-label={`Open ${title}`}
        onClick={(event) => {
          if (onOpenThread === undefined || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
            return;
          }
          event.preventDefault();
          onOpenThread(row);
        }}
        onFocus={() => onPrefetchThread?.(row.thread_id)}
        onMouseEnter={() => onPrefetchThread?.(row.thread_id)}
      >
        {rowCopy}
      </a>
    </li>
  );
}

export function formatGmailSender(value: string | null | undefined): string {
  const sender = value?.trim();
  if (!sender) {
    return 'Unknown sender';
  }

  const match = sender.match(/^"?([^"<]+?)"?\s*<([^>]+)>$/);
  if (match?.[1]) {
    return match[1].trim();
  }

  return sender;
}

function compactInboxText(value: string, maxLength: number): string {
  const compacted = value.replace(/\s+/g, ' ').trim();
  if (compacted.length <= maxLength) {
    return compacted;
  }

  return `${compacted.slice(0, maxLength - 3).trim()}...`;
}

export function formatGmailReceivedAt(value: string, now: Date = new Date()): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  const sameDay =
    date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate();

  if (!sameDay) {
    return new Intl.DateTimeFormat('en-US', {
      month: 'short',
      day: 'numeric',
    }).format(date);
  }

  return new Intl.DateTimeFormat('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  }).format(date);
}
