import type { ThreadMessage, ThreadReaderResponse } from '@decision-pipeline/types';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import React from 'react';

import { SignedInAppChrome } from '../../../../components/app/AppChrome';
import { getEntityThread, getGoogleAuthState } from '../../../../lib/api';
import { getServerCookieHeader } from '../../../../lib/server-cookies';

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
  threadId?: string;
};

export default async function EntityThreadPage({ params, searchParams }: EntityThreadPageProps) {
  const cookie = await getServerCookieHeader();
  const [{ entityId }, query] = await Promise.all([
    params,
    searchParams ?? Promise.resolve({}),
  ]);
  const page = parseThreadPageRequest(query);
  const [auth, threadResult] = await Promise.all([
    getGoogleAuthState({ cookie }),
    loadThreadDetail(entityId, page, cookie),
  ]);

  if (!auth.connected) {
    redirect('/');
  }

  return (
    <ThreadDetail
      entityId={entityId}
      page={page}
      thread={threadResult.thread}
      errorMessage={threadResult.errorMessage}
    />
  );
}

async function loadThreadDetail(entityId: string, page: ThreadPageRequest, cookie: string | null) {
  try {
    return {
      thread: await getEntityThread(entityId, page, { cookie }),
      errorMessage: null,
    };
  } catch (error) {
    return {
      thread: null,
      errorMessage: error instanceof Error ? error.message : 'Thread is unavailable.',
    };
  }
}

export function ThreadDetail({
  entityId,
  page = { limit: DEFAULT_THREAD_LIMIT, offset: 0 },
  thread,
  errorMessage,
  reader = 'entity',
}: {
  entityId: string;
  page?: ThreadPageRequest;
  thread: ThreadReaderResponse | null;
  errorMessage: string | null;
  reader?: 'entity' | 'gmail';
}) {
  const title = thread?.subject?.trim() || 'Thread';
  const pageState = thread ? getThreadPageState(thread, page) : null;

  return (
    <SignedInAppChrome active="gmail">
      <main className="digest-page">
        <div className="digest-shell thread-reader-shell">
          <header className="thread-reader-header">
            <Link className="thread-reader-back" href="/gmail" prefetch>
              Back to Inbox
            </Link>
            <h1 className="thread-reader-title">{title}</h1>
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
                <Link
                  className="thread-reader-page-link"
                  href={buildThreadPageHref(entityId, pageState.limit, pageState.previousOffset, page.threadId, reader)}
                >
                  Previous
                </Link>
              ) : null}
              {pageState.hasMore ? (
                <Link
                  className="thread-reader-page-link"
                  href={buildThreadPageHref(entityId, pageState.limit, pageState.nextOffset, page.threadId, reader)}
                >
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
    </SignedInAppChrome>
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

function buildThreadPageHref(
  entityId: string,
  limit: number,
  offset: number,
  threadId: string | undefined,
  reader: 'entity' | 'gmail',
): string {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (threadId !== undefined) {
    params.set('threadId', threadId);
  }
  if (reader === 'gmail') {
    return `/gmail/threads/${encodeURIComponent(threadId ?? entityId)}?${params.toString()}`;
  }
  return `/entities/${encodeURIComponent(entityId)}/thread?${params.toString()}`;
}

function parseThreadPageRequest(searchParams: Record<string, string | string[] | undefined>): ThreadPageRequest {
  return {
    limit: parseBoundedInteger(firstValue(searchParams.limit), DEFAULT_THREAD_LIMIT, 1, 100),
    offset: parseBoundedInteger(firstValue(searchParams.offset), 0, 0, Number.MAX_SAFE_INTEGER),
    threadId: cleanOptionalText(firstValue(searchParams.threadId)) || undefined,
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

function cleanOptionalText(value: string | undefined): string {
  return typeof value === 'string' ? value.trim() : '';
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
