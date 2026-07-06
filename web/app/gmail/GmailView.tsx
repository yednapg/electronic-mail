import React from 'react';
import type { CSSProperties } from 'react';

import { SignedInAppChrome } from '../../components/app/AppChrome';
import type { GmailThreadRow, GmailViewResponse } from '../../lib/types';

type GmailListProps = {
  gmail: GmailViewResponse;
  activeThreadId?: string | null;
  isSyncing?: boolean;
  onSync?: () => void;
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
  isSyncing = false,
  onSync,
  onOpenThread,
  onPrefetchThread,
}: GmailListProps) {
  return (
    <main className="digest-page">
      <div className="digest-shell gmail-shell gmail-inbox-shell">
        <header className="gmail-native-toolbar" aria-label="Inbox toolbar">
          <details className="gmail-native-menu">
            <summary className="gmail-native-icon-button" aria-label="Show navigation" title="Show navigation">
              <MenuIcon />
            </summary>
            <nav className="gmail-native-menu-popover" aria-label="Inbox navigation">
              <a href="/gmail">Inbox</a>
            </nav>
          </details>
          <h1 className="inbox-page-title">Inbox</h1>
          <div className="gmail-native-toolbar-actions">
            <button
              type="button"
              className="gmail-native-icon-button"
              aria-label="Compose email"
              title="Compose email"
              disabled
            >
              <ComposeIcon />
            </button>
            <button
              type="button"
              className="gmail-native-icon-button"
              aria-label={isSyncing ? 'Syncing mailbox' : 'Sync mailbox now'}
              title={isSyncing ? 'Syncing mailbox' : 'Sync mailbox now'}
              disabled={isSyncing || onSync === undefined}
              onClick={() => onSync?.()}
            >
              <SyncIcon />
            </button>
          </div>
        </header>

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
  const isExpandable = row.message_count > 1 || (row.children?.length ?? 0) > 1;
  const hasAttachments = Boolean(row.has_attachments || (row.attachment_count ?? 0) > 0);
  const rowCopy = (
    <>
      <span className={`gmail-thread-expand ${isExpandable ? '' : 'is-empty'}`} aria-hidden="true">
        {isExpandable ? <ExpandIcon /> : null}
      </span>
      <span className="gmail-thread-sender">{sender}</span>
      <span className="gmail-thread-message">{compactInboxText(title, 120)}</span>
      <span className={`gmail-thread-attachment ${hasAttachments ? '' : 'is-empty'}`} aria-hidden="true">
        {hasAttachments ? <PaperclipIcon /> : null}
      </span>
      <span className="gmail-thread-time">{formatGmailReceivedAt(row.latest_received_at)}</span>
    </>
  );

  return (
    <li className="gmail-thread-row" style={{ '--attention-index': index } as CSSProperties}>
      <a
        className={`gmail-thread-link ${isActive ? 'is-active' : ''} ${row.unread ? 'is-unread' : ''}`}
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

function ExpandIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="gmail-row-icon gmail-row-expand-icon">
      <circle cx="12" cy="12" r="8.4" />
      <path d="m10.4 8.3 4 3.7-4 3.7" />
    </svg>
  );
}

function PaperclipIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="gmail-row-icon">
      <path d="m8.5 12.8 5.8-5.8a3.2 3.2 0 0 1 4.5 4.5l-7.2 7.2a4.5 4.5 0 0 1-6.4-6.4l7.3-7.3" />
      <path d="m15.8 9.2-7.1 7.1a1.7 1.7 0 1 1-2.4-2.4l6.1-6.1" />
    </svg>
  );
}

function MenuIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="gmail-native-icon">
      <path d="M4 7h16M4 12h16M4 17h16" />
    </svg>
  );
}

function ComposeIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="gmail-native-icon">
      <path d="M5 19h14" />
      <path d="M7 17.2 16.8 7.4a2.1 2.1 0 0 1 3 3L10 20l-4.2.8Z" />
      <path d="m15.4 8.8 3 3" />
    </svg>
  );
}

function SyncIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="gmail-native-icon">
      <path d="M20 11a8 8 0 0 0-13.7-5.7L4 7.6" />
      <path d="M4 4v3.6h3.6" />
      <path d="M4 13a8 8 0 0 0 13.7 5.7L20 16.4" />
      <path d="M20 20v-3.6h-3.6" />
    </svg>
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
