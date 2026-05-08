import type { ThreadMessage, ThreadReaderResponse } from '@decision-pipeline/types';
import Link from 'next/link';
import React from 'react';

import { getEntityThread } from '../../../../lib/api';

type EntityThreadPageProps = {
  params: Promise<{
    entityId: string;
  }>;
};

export default async function EntityThreadPage({ params }: EntityThreadPageProps) {
  const { entityId } = await params;
  let thread: ThreadReaderResponse | null = null;
  let errorMessage: string | null = null;

  try {
    thread = await getEntityThread(entityId);
  } catch (error) {
    errorMessage = error instanceof Error ? error.message : 'Thread is unavailable.';
  }

  return <ThreadDetail entityId={entityId} thread={thread} errorMessage={errorMessage} />;
}

export function ThreadDetail({
  entityId,
  thread,
  errorMessage,
}: {
  entityId: string;
  thread: ThreadReaderResponse | null;
  errorMessage: string | null;
}) {
  const title = thread?.subject?.trim() || 'Thread';

  return (
    <main className="digest-page">
      <div className="digest-shell thread-reader-shell">
        <header className="thread-reader-header">
          <Link className="thread-reader-back" href="/dashboard">
            Dashboard
          </Link>
          <h1 className="thread-reader-title">{title}</h1>
          {thread ? (
            <div className="thread-reader-meta" aria-label="Thread metadata">
              {buildThreadMeta(thread).map((part) => (
                <span key={part}>{part}</span>
              ))}
            </div>
          ) : null}
          {errorMessage ? <p className="thread-reader-empty">{errorMessage}</p> : null}
        </header>

        {thread && thread.messages.length > 0 ? (
          <ol className="thread-message-list" aria-label="Persisted thread messages">
            {thread.messages.map((message) => (
              <ThreadMessageCard key={message.id} message={message} />
            ))}
          </ol>
        ) : null}

        {thread && thread.messages.length === 0 ? (
          <p className="thread-reader-empty">No persisted messages are attached to this entity yet.</p>
        ) : null}
      </div>
    </main>
  );
}

function ThreadMessageCard({ message }: { message: ThreadMessage }) {
  const subject = message.subject?.trim() || 'Untitled message';
  const body = message.body.trim();
  const source = formatSource(message.source);

  return (
    <li className="thread-message">
      <article className="thread-message-card" aria-label={subject}>
        <header className="thread-message-header">
          <h2 className="thread-message-subject">{subject}</h2>
          <p className="thread-message-source">{source} · {formatReceivedAt(message.received_at)}</p>
        </header>
        <dl className="thread-message-fields">
          {message.from_address ? (
            <>
              <dt>From</dt>
              <dd>{message.from_address}</dd>
            </>
          ) : null}
          {message.to ? (
            <>
              <dt>To</dt>
              <dd>{message.to}</dd>
            </>
          ) : null}
          {message.cc ? (
            <>
              <dt>Cc</dt>
              <dd>{message.cc}</dd>
            </>
          ) : null}
          {message.bcc ? (
            <>
              <dt>Bcc</dt>
              <dd>{message.bcc}</dd>
            </>
          ) : null}
        </dl>
        {message.snippet ? <p className="thread-message-snippet">{message.snippet}</p> : null}
        <p className="thread-message-body">{body.length > 0 ? body : 'No persisted body text.'}</p>
      </article>
    </li>
  );
}

function buildThreadMeta(thread: ThreadReaderResponse): string[] {
  const parts = [`${thread.messages.length} ${thread.messages.length === 1 ? 'email' : 'emails'}`];
  const source = thread.source ? formatSource(thread.source) : null;
  if (source) {
    parts.push(source);
  }

  const received = thread.messages.map((message) => message.received_at).filter(Boolean);
  if (received.length > 0) {
    const first = received[0];
    const last = received[received.length - 1];
    parts.push(first === last ? formatReceivedAt(first) : `${formatReceivedAt(first)} to ${formatReceivedAt(last)}`);
  }

  return parts;
}

function formatSource(source: ThreadMessage['source']): string {
  if (source === 'gmail') {
    return 'Gmail';
  }

  if (source === 'calendar') {
    return 'Calendar';
  }

  return 'Manual';
}

function formatReceivedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat('en-IN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}
