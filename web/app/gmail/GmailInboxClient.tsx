'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { SignedInAppChrome } from '../../components/app/AppChrome';
import { useAppSession } from '../../lib/app-session-store';
import { getDemoThreadReader } from '../../lib/demo-data';
import { isDemoMode } from '../../lib/demo-mode';
import { useActiveMailboxSync } from '../../lib/use-active-mailbox-sync';
import type { GmailThreadRow, MailboxResponse, ThreadMessage, ThreadReaderResponse } from '../../lib/types';
import { GmailList, formatGmailSender } from './GmailView';

const THREAD_STORAGE_PREFIX = 'electronic-mail-mailbox-thread:v3:';
const DEFAULT_BROWSER_BACKEND_URL = 'http://localhost:3001';
const THREAD_FETCH_LIMIT = 50;
const PREFETCH_THREAD_COUNT = 10;

let inMemoryThreadCache: { userKey: string; threads: Record<string, ThreadReaderResponse> } | null = null;
type ActiveRowSource = 'keyboard' | 'programmatic';

type GmailInboxClientProps = {
  initialThreadId?: string | null;
  initialMailbox?: MailboxResponse | null;
};

export function GmailInboxClient({ initialThreadId = null, initialMailbox = null }: GmailInboxClientProps) {
  const router = useRouter();
  const { session, refreshFailed: appSessionRefreshFailed } = useAppSession();
  const [gmail, setGmail] = useState<MailboxResponse | null>(() => session?.mailbox ?? initialMailbox);
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(() => cleanThreadId(initialThreadId));
  const [threadCache, setThreadCache] = useState<Record<string, ThreadReaderResponse>>(
    () => inMemoryThreadCache?.threads ?? {},
  );
  const [threadErrors, setThreadErrors] = useState<Record<string, string>>({});
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const [cacheUserKey, setCacheUserKey] = useState<string | null>(() => session?.user.id ?? null);
  const threadCacheRef = useRef(threadCache);
  const gmailRef = useRef(gmail);
  const inFlightThreadsRef = useRef(new Set<string>());
  const activeRowFrameRef = useRef<number | null>(null);
  const activePrefetchTimeoutRef = useRef<number | null>(null);
  const activeRowSourceRef = useRef<ActiveRowSource>('programmatic');

  useActiveMailboxSync(Boolean(session?.dashboard.auth.connected));

  useEffect(() => {
    threadCacheRef.current = threadCache;
  }, [threadCache]);

  useEffect(() => {
    gmailRef.current = gmail;
  }, [gmail]);

  useEffect(() => {
    return () => {
      if (activeRowFrameRef.current !== null) {
        window.cancelAnimationFrame(activeRowFrameRef.current);
      }
      if (activePrefetchTimeoutRef.current !== null) {
        window.clearTimeout(activePrefetchTimeoutRef.current);
      }
    };
  }, []);

  useEffect(() => {
    router.prefetch('/dashboard');
  }, [router]);

  useEffect(() => {
    if (session === null && appSessionRefreshFailed) {
      router.replace('/');
    }
  }, [appSessionRefreshFailed, router, session]);

  const fetchThread = useCallback((threadId: string, options: { force?: boolean; silent?: boolean } = {}) => {
    const cleanId = cleanThreadId(threadId);
    if (cleanId === null) {
      return;
    }

    const cachedThread = threadCacheRef.current[cleanId] ?? (
      cacheUserKey === null ? null : readCachedThread(cacheUserKey, cleanId)
    );
    if (cachedThread !== null && threadCacheRef.current[cleanId] === undefined) {
      setThreadCache((current) => ({ ...current, [cleanId]: cachedThread }));
    }
    if (!options.force && cachedThread !== null) {
      return;
    }
    if (inFlightThreadsRef.current.has(cleanId)) {
      return;
    }

    inFlightThreadsRef.current.add(cleanId);
    fetchMailboxThread(cleanId)
      .then((thread) => {
        if (thread === null) {
          router.replace('/');
          return;
        }
        setThreadCache((current) => ({ ...current, [cleanId]: thread }));
        setThreadErrors((current) => omitKey(current, cleanId));
        if (cacheUserKey !== null) {
          inMemoryThreadCache = {
            userKey: cacheUserKey,
            threads: {
              ...(inMemoryThreadCache?.userKey === cacheUserKey ? inMemoryThreadCache.threads : {}),
              [cleanId]: thread,
            },
          };
          writeCachedThread(cacheUserKey, cleanId, thread);
        }
      })
      .catch(() => {
        if (!options.silent) {
          setThreadErrors((current) => ({
            ...current,
            [cleanId]: 'Full thread could not refresh.',
          }));
        }
      })
      .finally(() => {
        inFlightThreadsRef.current.delete(cleanId);
      });
  }, [cacheUserKey, router]);

  useEffect(() => {
    if (session === null) {
      return;
    }
    const nextCacheUserKey = session.user.id;
    setCacheUserKey(nextCacheUserKey);
    setGmail((current) => {
      const nextMailbox = session.mailbox;
      if (current !== null && current.total_threads > 0 && nextMailbox.total_threads === 0) {
        return current;
      }
      return nextMailbox;
    });
    setRefreshFailed(false);
    if (inMemoryThreadCache?.userKey === nextCacheUserKey) {
      setThreadCache(inMemoryThreadCache.threads);
    }
  }, [session]);

  useEffect(() => {
    const nextSelectedThreadId = cleanThreadId(initialThreadId);
    setSelectedThreadId(nextSelectedThreadId);
  }, [initialThreadId]);

  useEffect(() => {
    const handlePopState = () => {
      setSelectedThreadId(readThreadIdFromLocation());
    };
    window.addEventListener('popstate', handlePopState);
    return () => {
      window.removeEventListener('popstate', handlePopState);
    };
  }, []);

  useEffect(() => {
    if (selectedThreadId === null) {
      return;
    }
    fetchThread(selectedThreadId, { force: true });
  }, [fetchThread, selectedThreadId]);

  const mailboxRows = useMemo(() => (gmail === null ? [] : flattenMailboxRows(gmail)), [gmail]);

  const activateThread = useCallback((threadId: string, source: ActiveRowSource) => {
    activeRowSourceRef.current = source;
    setActiveThreadId(threadId);
  }, []);

  useEffect(() => {
    if (mailboxRows.length === 0) {
      setActiveThreadId(null);
      return;
    }

    setActiveThreadId((current) => {
      if (current !== null && mailboxRows.some((row) => row.thread_id === current)) {
        return current;
      }
      return null;
    });
  }, [mailboxRows]);

  useEffect(() => {
    if (mailboxRows.length === 0) {
      return;
    }
    const threadIds = mailboxRows
      .slice(0, PREFETCH_THREAD_COUNT)
      .map((row) => row.thread_id);
    const timeoutId = window.setTimeout(() => {
      threadIds.forEach((threadId) => fetchThread(threadId, { silent: true }));
    }, 0);
    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [fetchThread, mailboxRows]);

  useEffect(() => {
    if (activeThreadId === null || selectedThreadId !== null || mailboxRows.length === 0) {
      return;
    }
    if (!mailboxRows.some((row) => row.thread_id === activeThreadId)) {
      return;
    }
    if (activeRowSourceRef.current !== 'keyboard') {
      return;
    }

    if (activeRowFrameRef.current !== null) {
      window.cancelAnimationFrame(activeRowFrameRef.current);
    }
    activeRowFrameRef.current = window.requestAnimationFrame(() => {
      activeRowFrameRef.current = null;
      const activeLink = document.querySelector<HTMLAnchorElement>(
        `[data-gmail-thread-id="${escapeSelectorValue(activeThreadId)}"]`,
      );
      if (activeLink === null) {
        return;
      }
      activeLink.scrollIntoView({
        block: 'nearest',
        inline: 'nearest',
        behavior: 'auto',
      });
    });

    if (activePrefetchTimeoutRef.current !== null) {
      window.clearTimeout(activePrefetchTimeoutRef.current);
    }
    activePrefetchTimeoutRef.current = window.setTimeout(() => {
      activePrefetchTimeoutRef.current = null;
      fetchThread(activeThreadId, { silent: true });
    }, 90);

    return () => {
      if (activeRowFrameRef.current !== null) {
        window.cancelAnimationFrame(activeRowFrameRef.current);
        activeRowFrameRef.current = null;
      }
      if (activePrefetchTimeoutRef.current !== null) {
        window.clearTimeout(activePrefetchTimeoutRef.current);
        activePrefetchTimeoutRef.current = null;
      }
    };
  }, [activeThreadId, fetchThread, mailboxRows, selectedThreadId]);

  const selectedRow = useMemo(() => {
    if (selectedThreadId === null) {
      return null;
    }
    return mailboxRows.find((row) => row.thread_id === selectedThreadId) ?? null;
  }, [mailboxRows, selectedThreadId]);
  const selectedThread = selectedThreadId === null
    ? null
    : threadCache[selectedThreadId] ?? (selectedRow ? buildPreviewThread(selectedRow) : null);
  const selectedError = selectedThreadId ? threadErrors[selectedThreadId] ?? null : null;

  const openThread = useCallback((row: GmailThreadRow) => {
    setSelectedThreadId(row.thread_id);
    activateThread(row.thread_id, 'programmatic');
    setThreadErrors((current) => omitKey(current, row.thread_id));
    pushMailboxURL(row.thread_id);
    fetchThread(row.thread_id);
  }, [activateThread, fetchThread]);

  const closeThread = useCallback(() => {
    setSelectedThreadId(null);
    pushMailboxURL(null);
  }, []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (isEditableTarget(event.target)) {
        return;
      }

      if (selectedThreadId !== null) {
        if (event.key === 'Escape' || event.key === 'Backspace') {
          event.preventDefault();
          closeThread();
        }
        return;
      }

      if (mailboxRows.length === 0) {
        return;
      }

      const activeIndex = activeThreadId === null
        ? -1
        : mailboxRows.findIndex((row) => row.thread_id === activeThreadId);
      if (event.key === 'ArrowDown' || event.key.toLowerCase() === 'j') {
        event.preventDefault();
        const nextIndex = activeIndex < 0 ? 0 : Math.min(activeIndex + 1, mailboxRows.length - 1);
        const nextRow = mailboxRows[nextIndex];
        if (nextRow) {
          activateThread(nextRow.thread_id, 'keyboard');
        }
        return;
      }
      if (event.key === 'ArrowUp' || event.key.toLowerCase() === 'k') {
        event.preventDefault();
        const nextIndex = activeIndex < 0 ? mailboxRows.length - 1 : Math.max(activeIndex - 1, 0);
        const nextRow = mailboxRows[nextIndex];
        if (nextRow) {
          activateThread(nextRow.thread_id, 'keyboard');
        }
        return;
      }
      if (event.key === 'Enter' || event.key.toLowerCase() === 'o') {
        if (activeIndex < 0) {
          return;
        }
        const row = mailboxRows[activeIndex];
        if (row) {
          event.preventDefault();
          openThread(row);
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [activateThread, activeThreadId, closeThread, mailboxRows, openThread, selectedThreadId]);

  if (gmail !== null || selectedThreadId !== null) {
    return (
      <SignedInAppChrome active="gmail">
        {selectedThreadId ? (
          <GmailThreadPanel
            errorMessage={selectedError}
            row={selectedRow}
            thread={selectedThread}
            threadId={selectedThreadId}
            onBack={closeThread}
          />
        ) : gmail !== null ? (
          <GmailList
            gmail={gmail}
            activeThreadId={activeThreadId}
            onOpenThread={openThread}
            onPrefetchThread={(threadId) => fetchThread(threadId, { silent: true })}
          />
        ) : (
          <MailboxLoadingView />
        )}
        {session?.mailbox.full_import_running ? (
          <p className="inbox-refresh-status" role="status">
            Importing older mail in background.
          </p>
        ) : (session?.mailbox.pending_count ?? 0) > 0 ? (
          <p className="inbox-refresh-status" role="status">
            Finishing AI titles for older mail.
          </p>
        ) : null}
        {refreshFailed || appSessionRefreshFailed ? (
          <p className="inbox-refresh-status" role="status">
            Inbox could not refresh. Showing last saved state.
          </p>
        ) : null}
      </SignedInAppChrome>
    );
  }

  return (
    <SignedInAppChrome active="gmail">
      <MailboxLoadingView />
    </SignedInAppChrome>
  );
}

function MailboxLoadingView() {
  return (
    <main className="digest-page">
      <div className="digest-shell gmail-shell">
        <div className="inbox-topline">
          <p className="inbox-count">Refreshing</p>
        </div>
        <h1 className="inbox-page-title">Inbox</h1>
        <p className="inbox-empty">Loading inbox...</p>
      </div>
    </main>
  );
}

function GmailThreadPanel({
  threadId,
  row,
  thread,
  errorMessage,
  onBack,
}: {
  threadId: string;
  row: GmailThreadRow | null;
  thread: ThreadReaderResponse | null;
  errorMessage: string | null;
  onBack: () => void;
}) {
  const title = thread?.subject?.trim() || row?.latest_subject?.trim() || 'Thread';
  const summary = row?.summary?.trim() || row?.snippet?.trim() || null;
  const messages = thread?.messages ?? [];

  return (
    <main className="digest-page">
      <div className="digest-shell thread-reader-shell">
        <header className="thread-reader-header">
          <button
            type="button"
            className="thread-reader-back"
            onClick={() => {
              onBack();
            }}
          >
            Back to Inbox
          </button>
          <h1 className="thread-reader-title">{title}</h1>
          {summary ? <p className="thread-reader-summary">{summary}</p> : null}
          {errorMessage ? <p className="thread-reader-empty" role="status">{errorMessage}</p> : null}
        </header>

        {messages.length > 0 ? (
          <ol className="thread-message-list" aria-label="Persisted thread messages">
            {messages.map((message) => (
              <GmailThreadMessageCard key={message.id} message={message} />
            ))}
          </ol>
        ) : (
          <p className="thread-reader-empty">Loading thread {threadId}...</p>
        )}
      </div>
    </main>
  );
}

function GmailThreadMessageCard({ message }: { message: ThreadMessage }) {
  const subject = message.subject?.trim() || 'Untitled message';
  const body = message.body.trim();
  const htmlBody = message.html_body?.trim();
  const sender = message.from_address?.trim() || 'Unknown sender';

  return (
    <li className="thread-message">
      <article className="thread-message-card" aria-label={subject}>
        <header className="thread-message-header">
          <h2 className="thread-message-subject">{sender}</h2>
          <p className="thread-message-source">{formatThreadReceivedAt(message.received_at)}</p>
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
        {htmlBody ? (
          <iframe
            className="thread-message-html-frame"
            title={subject}
            sandbox=""
            srcDoc={htmlBody}
          />
        ) : (
          <p className="thread-message-body">{body.length > 0 ? body : 'No persisted body text.'}</p>
        )}
      </article>
    </li>
  );
}

function buildPreviewThread(row: GmailThreadRow): ThreadReaderResponse {
  const body = row.summary || row.snippet || 'Thread preview is loading.';
  return {
    entity_id: row.entity_id ?? `gmail-thread:${row.thread_id}`,
    user_id: '',
    source: 'gmail',
    gmail_thread_id: row.thread_id,
    subject: row.latest_subject,
    total_messages: Math.max(row.message_count, 1),
    limit: THREAD_FETCH_LIMIT,
    offset: 0,
    has_more: false,
    messages: [
      {
        id: row.latest_source_record_id,
        source: 'gmail',
        thread_id: row.thread_id,
        from_address: row.latest_sender ?? formatGmailSender(row.latest_sender),
        to: null,
        cc: null,
        bcc: null,
        subject: row.latest_subject,
        body,
        snippet: row.snippet ?? row.summary ?? null,
        label_ids: row.label_ids ?? [],
        received_at: row.latest_received_at,
      },
    ],
  };
}

function flattenMailboxRows(gmail: MailboxResponse): GmailThreadRow[] {
  return gmail.sections.flatMap((section) => section.rows);
}

function escapeSelectorValue(value: string): string {
  if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
    return CSS.escape(value);
  }
  return value.replace(/["\\]/g, '\\$&');
}

async function fetchMailboxThread(threadId: string): Promise<ThreadReaderResponse | null> {
  if (isDemoMode()) {
    return getDemoThreadReader(threadId);
  }
  const query = `limit=${THREAD_FETCH_LIMIT}`;
  const urls = [
    `${getBrowserBackendURL()}/v1/mailbox/threads/${encodeURIComponent(threadId)}?${query}`,
    `/api/mailbox/threads/${encodeURIComponent(threadId)}?${query}`,
  ];

  let lastError: unknown = null;
  for (const url of urls) {
    try {
      const response = await fetch(url, {
        cache: 'no-store',
        credentials: 'include',
        headers: {
          Accept: 'application/json',
        },
      });
      if (response.status === 401) {
        return null;
      }
      if (!response.ok) {
        throw new Error('Thread could not refresh.');
      }
      return response.json() as Promise<ThreadReaderResponse>;
    } catch (error) {
      lastError = error;
    }
  }

  throw lastError instanceof Error ? lastError : new Error('Thread could not refresh.');
}

function getBrowserBackendURL(): string {
  return (process.env.NEXT_PUBLIC_ELECTRONIC_MAIL_BACKEND_URL ?? DEFAULT_BROWSER_BACKEND_URL).replace(/\/+$/, '');
}

function readCachedThread(userKey: string, threadId: string): ThreadReaderResponse | null {
  try {
    const cached = window.localStorage.getItem(threadStorageKey(userKey, threadId));
    if (cached === null) {
      return null;
    }
    const parsed = JSON.parse(cached) as Partial<ThreadReaderResponse>;
    if (typeof parsed.entity_id !== 'string' || !Array.isArray(parsed.messages)) {
      return null;
    }
    return parsed as ThreadReaderResponse;
  } catch (_error) {
    return null;
  }
}

function writeCachedThread(userKey: string, threadId: string, thread: ThreadReaderResponse) {
  try {
    window.localStorage.setItem(threadStorageKey(userKey, threadId), JSON.stringify(thread));
  } catch (_error) {}
}

function threadStorageKey(userKey: string, threadId: string): string {
  return `${THREAD_STORAGE_PREFIX}${userKey}:${threadId}`;
}

function cleanThreadId(value: string | null | undefined): string | null {
  const cleaned = value?.trim();
  return cleaned ? cleaned : null;
}

function readThreadIdFromLocation(): string | null {
  const match = window.location.pathname.match(/^\/gmail\/threads\/([^/]+)$/);
  return match ? decodeURIComponent(match[1]) : null;
}

function pushMailboxURL(threadId: string | null) {
  const nextURL = threadId === null ? '/gmail' : `/gmail/threads/${encodeURIComponent(threadId)}`;
  const currentURL = `${window.location.pathname}${window.location.search}`;
  if (currentURL !== nextURL) {
    window.history.pushState({ threadId }, '', nextURL);
  }
}

function formatThreadReceivedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date);
}

function omitKey<T>(values: Record<string, T>, key: string): Record<string, T> {
  if (values[key] === undefined) {
    return values;
  }
  const next = { ...values };
  delete next[key];
  return next;
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }

  const tagName = target.tagName.toLowerCase();
  return target.isContentEditable || tagName === 'input' || tagName === 'textarea' || tagName === 'select';
}
