import type { ThreadMessage, ThreadReaderResponse } from '@decision-pipeline/types';
import Link from 'next/link';
import React from 'react';

import { getEntityThread } from '../../../../lib/api';

const DEFAULT_THREAD_LIMIT = 25;

type EntityThreadPageProps = {
  params: Promise<{
    entityId: string;
  }>;
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

type ThreadPageRequest = {
  limit: number;
  offset: number;
};

export default async function EntityThreadPage({ params, searchParams }: EntityThreadPageProps) {
  const [{ entityId }, query] = await Promise.all([params, searchParams ?? Promise.resolve({})]);
  const page = parseThreadPageRequest(query);
  let thread: ThreadReaderResponse | null = null;
  let errorMessage: string | null = null;

  try {
    thread = await getEntityThread(entityId, page);
  } catch (error) {
    errorMessage = error instanceof Error ? error.message : 'Thread is unavailable.';
  }

  return <ThreadDetail entityId={entityId} page={page} thread={thread} errorMessage={errorMessage} />;
}

export function ThreadDetail({
  entityId,
  page = { limit: DEFAULT_THREAD_LIMIT, offset: 0 },
  thread,
  errorMessage,
}: {
  entityId: string;
  page?: ThreadPageRequest;
  thread: ThreadReaderResponse | null;
  errorMessage: string | null;
}) {
  const title = thread?.subject?.trim() || 'Thread';
  const pageState = thread ? getThreadPageState(thread, page) : null;

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
              {buildThreadMeta(thread, pageState).map((part) => (
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

        {thread && pageState && (pageState.hasPrevious || pageState.hasMore) ? (
          <nav className="thread-reader-pagination" aria-label="Thread pages">
            {pageState.hasPrevious ? (
              <Link className="thread-reader-page-link" href={buildThreadPageHref(entityId, pageState.limit, pageState.previousOffset)}>
                Previous
              </Link>
            ) : null}
            {pageState.hasMore ? (
              <Link className="thread-reader-page-link" href={buildThreadPageHref(entityId, pageState.limit, pageState.nextOffset)}>
                More
              </Link>
            ) : null}
          </nav>
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

function buildThreadMeta(thread: ThreadReaderResponse, pageState: ReturnType<typeof getThreadPageState> | null): string[] {
  const messageCount = thread.messages.length;
  const totalMessages = pageState?.totalMessages ?? messageCount;
  const emailLabel = totalMessages === 1 ? 'email' : 'emails';
  const parts =
    totalMessages > messageCount
      ? [`${messageCount} of ${totalMessages} ${emailLabel}`]
      : [`${messageCount} ${messageCount === 1 ? 'email' : 'emails'}`];
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

function getThreadPageState(thread: ThreadReaderResponse, requestedPage: ThreadPageRequest) {
  const messageCount = thread.messages.length;
  const limit = positiveNumber(thread.limit) ?? requestedPage.limit;
  const offset = nonNegativeNumber(thread.offset) ?? requestedPage.offset;
  const totalMessages = Math.max(nonNegativeNumber(thread.total_messages) ?? messageCount, messageCount);
  const hasMore = typeof thread.has_more === 'boolean' ? thread.has_more : offset + messageCount < totalMessages;

  return {
    limit,
    offset,
    totalMessages,
    hasMore,
    hasPrevious: offset > 0,
    previousOffset: Math.max(0, offset - limit),
    nextOffset: offset + limit,
  };
}

function buildThreadPageHref(entityId: string, limit: number, offset: number): string {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  return `/entities/${encodeURIComponent(entityId)}/thread?${params.toString()}`;
}

function parseThreadPageRequest(searchParams: Record<string, string | string[] | undefined>): ThreadPageRequest {
  return {
    limit: parseBoundedInteger(firstValue(searchParams.limit), DEFAULT_THREAD_LIMIT, 1, 100),
    offset: parseBoundedInteger(firstValue(searchParams.offset), 0, 0, Number.MAX_SAFE_INTEGER),
  };
}

function firstValue(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function parseBoundedInteger(value: string | undefined, fallback: number, min: number, max: number): number {
  if (value === undefined) {
    return fallback;
  }

  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < min) {
    return fallback;
  }

  return Math.min(parsed, max);
}

function positiveNumber(value: number | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
}

function nonNegativeNumber(value: number | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
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
