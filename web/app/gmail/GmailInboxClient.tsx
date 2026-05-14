'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { SignedInAppChrome } from '../../components/app/AppChrome';
import type { AuthMeResponse, GmailThreadRow, GoogleAuthState, MailboxResponse, ThreadMessage, ThreadReaderResponse } from '../../lib/types';
import { GmailList, formatGmailSender } from './GmailView';

const MAILBOX_STORAGE_PREFIX = 'electronic-mail-mailbox:inbox:v3:';
const MAILBOX_SYNC_STORAGE_PREFIX = 'electronic-mail-mailbox-sync:v2:';
const THREAD_STORAGE_PREFIX = 'electronic-mail-mailbox-thread:v3:';
const DEFAULT_BROWSER_BACKEND_URL = 'http://localhost:3001';
const MAILBOX_FETCH_LIMIT = 150;
const THREAD_FETCH_LIMIT = 50;
const PREFETCH_THREAD_COUNT = 10;
const MAILBOX_SYNC_COOLDOWN_MS = 30_000;

let inMemoryMailboxCache: { userKey: string; gmail: MailboxResponse } | null = null;
let inMemoryThreadCache: { userKey: string; threads: Record<string, ThreadReaderResponse> } | null = null;

type GmailInboxClientProps = {
  initialThreadId?: string | null;
};

export function GmailInboxClient({ initialThreadId = null }: GmailInboxClientProps) {
  const router = useRouter();
  const [gmail, setGmail] = useState<MailboxResponse | null>(() => inMemoryMailboxCache?.gmail ?? null);
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(() => cleanThreadId(initialThreadId));
  const [threadCache, setThreadCache] = useState<Record<string, ThreadReaderResponse>>(
    () => inMemoryThreadCache?.threads ?? {},
  );
  const [threadErrors, setThreadErrors] = useState<Record<string, string>>({});
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const [cacheUserKey, setCacheUserKey] = useState<string | null>(() => inMemoryMailboxCache?.userKey ?? null);
  const threadCacheRef = useRef(threadCache);
  const inFlightThreadsRef = useRef(new Set<string>());

  useEffect(() => {
    threadCacheRef.current = threadCache;
  }, [threadCache]);

  useEffect(() => {
    router.prefetch('/dashboard');
  }, [router]);

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
    let cancelled = false;

    void fetchAuthMe()
      .then((me) => {
        if (cancelled) {
          return;
        }
        if (!me.authenticated || me.user === undefined || me.user === null) {
          clearMailboxCaches();
          router.replace('/');
          return;
        }
        const nextCacheUserKey = me.user.id;
        setCacheUserKey(nextCacheUserKey);
        const cachedMailbox = inMemoryMailboxCache?.userKey === nextCacheUserKey
          ? inMemoryMailboxCache.gmail
          : readCachedMailbox(nextCacheUserKey);
        if (cachedMailbox !== null) {
          setGmail(cachedMailbox);
        } else if (inMemoryMailboxCache?.userKey !== nextCacheUserKey) {
          setGmail(null);
        }
        if (inMemoryThreadCache?.userKey === nextCacheUserKey) {
          setThreadCache(inMemoryThreadCache.threads);
        } else {
          setThreadCache({});
        }

        void maybeTriggerMailboxSync(nextCacheUserKey);

        return fetchMailbox()
          .then((nextGmail) => {
            if (cancelled || nextGmail === null) {
              return;
            }
            setGmail(nextGmail);
            setRefreshFailed(false);
            inMemoryMailboxCache = { userKey: nextCacheUserKey, gmail: nextGmail };
            writeCachedMailbox(nextCacheUserKey, nextGmail);
          });
      })
      .catch(() => {
        if (!cancelled) {
          void verifyGoogleConnection()
            .then((auth) => {
              if (auth.connected) {
                return;
              }
              clearMailboxCaches();
              if (auth.connect_url) {
                window.location.assign(auth.connect_url);
                return;
              }
              router.replace('/');
            })
            .catch(() => {});
          setRefreshFailed(true);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [router]);

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

  useEffect(() => {
    if (mailboxRows.length === 0) {
      setActiveThreadId(null);
      return;
    }

    setActiveThreadId((current) => {
      if (current !== null && mailboxRows.some((row) => row.thread_id === current)) {
        return current;
      }
      return mailboxRows[0]?.thread_id ?? null;
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
    setActiveThreadId(row.thread_id);
    setThreadErrors((current) => omitKey(current, row.thread_id));
    pushMailboxURL(row.thread_id);
    fetchThread(row.thread_id);
  }, [fetchThread]);

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

      const activeIndex = Math.max(0, mailboxRows.findIndex((row) => row.thread_id === activeThreadId));
      if (event.key === 'ArrowDown' || event.key.toLowerCase() === 'j') {
        event.preventDefault();
        const nextRow = mailboxRows[Math.min(activeIndex + 1, mailboxRows.length - 1)];
        if (nextRow) {
          setActiveThreadId(nextRow.thread_id);
          fetchThread(nextRow.thread_id, { silent: true });
        }
        return;
      }
      if (event.key === 'ArrowUp' || event.key.toLowerCase() === 'k') {
        event.preventDefault();
        const nextRow = mailboxRows[Math.max(activeIndex - 1, 0)];
        if (nextRow) {
          setActiveThreadId(nextRow.thread_id);
          fetchThread(nextRow.thread_id, { silent: true });
        }
        return;
      }
      if (event.key === 'Enter' || event.key.toLowerCase() === 'o') {
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
  }, [activeThreadId, closeThread, fetchThread, mailboxRows, openThread, selectedThreadId]);

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
            onActivateThread={setActiveThreadId}
            onOpenThread={openThread}
            onPrefetchThread={(threadId) => fetchThread(threadId, { silent: true })}
          />
        ) : (
          <MailboxLoadingView />
        )}
        {refreshFailed ? (
          <p className="inbox-refresh-status" role="status">
            Inbox could not refresh.
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

  return (
    <li className="thread-message">
      <article className="thread-message-card" aria-label={subject}>
        <header className="thread-message-header">
          <h2 className="thread-message-subject">{subject}</h2>
          <p className="thread-message-source">Gmail · {formatThreadReceivedAt(message.received_at)}</p>
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

function readCachedMailbox(userKey: string): MailboxResponse | null {
  try {
    const cached = window.localStorage.getItem(mailboxStorageKey(userKey));
    if (cached === null) {
      return null;
    }

    const parsed = JSON.parse(cached) as Partial<MailboxResponse>;
    if (parsed.label !== 'inbox' || typeof parsed.total_threads !== 'number' || !Array.isArray(parsed.sections)) {
      return null;
    }

    return parsed as MailboxResponse;
  } catch (_error) {
    return null;
  }
}

function writeCachedMailbox(userKey: string, gmail: MailboxResponse) {
  try {
    window.localStorage.setItem(mailboxStorageKey(userKey), JSON.stringify(gmail));
  } catch (_error) {}
}

async function fetchMailbox(): Promise<MailboxResponse | null> {
  const query = `label=inbox&limit=${MAILBOX_FETCH_LIMIT}`;
  const urls = [
    `${getBrowserBackendURL()}/v1/mailbox?${query}`,
    `/api/mailbox?${query}`,
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
        throw new Error('Gmail inbox refresh failed');
      }
      return response.json() as Promise<MailboxResponse>;
    } catch (error) {
      lastError = error;
    }
  }

  throw lastError instanceof Error ? lastError : new Error('Gmail inbox refresh failed');
}

async function fetchMailboxThread(threadId: string): Promise<ThreadReaderResponse | null> {
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

async function verifyGoogleConnection(): Promise<GoogleAuthState> {
  const response = await fetch(`${getBrowserBackendURL()}/v1/auth/google/state`, {
    cache: 'no-store',
    credentials: 'include',
    headers: {
      Accept: 'application/json',
    },
  });
  if (!response.ok) {
    throw new Error('Google auth state could not refresh.');
  }
  return response.json() as Promise<GoogleAuthState>;
}

async function fetchAuthMe(): Promise<AuthMeResponse> {
  const response = await fetch(`${getBrowserBackendURL()}/v1/auth/me`, {
    cache: 'no-store',
    credentials: 'include',
    headers: {
      Accept: 'application/json',
    },
  });
  if (!response.ok) {
    throw new Error('Session could not refresh.');
  }
  return response.json() as Promise<AuthMeResponse>;
}

async function maybeTriggerMailboxSync(userKey: string): Promise<void> {
  if (!shouldTriggerMailboxSync(userKey)) {
    return;
  }

  const urls = [
    `${getBrowserBackendURL()}/v1/mailbox/sync`,
    '/api/mailbox/sync',
  ];

  for (const url of urls) {
    try {
      const response = await fetch(url, {
        method: 'POST',
        cache: 'no-store',
        credentials: 'include',
        headers: {
          Accept: 'application/json',
        },
      });
      if (response.ok) {
        writeLastMailboxSyncAttempt(userKey);
        return;
      }
    } catch (_error) {}
  }
}

function shouldTriggerMailboxSync(userKey: string): boolean {
  try {
    const rawValue = window.localStorage.getItem(mailboxSyncStorageKey(userKey));
    const lastAttempt = rawValue === null ? 0 : Number(rawValue);
    return !Number.isFinite(lastAttempt) || Date.now() - lastAttempt > MAILBOX_SYNC_COOLDOWN_MS;
  } catch (_error) {
    return true;
  }
}

function writeLastMailboxSyncAttempt(userKey: string) {
  try {
    window.localStorage.setItem(mailboxSyncStorageKey(userKey), String(Date.now()));
  } catch (_error) {}
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

function clearMailboxCaches() {
  inMemoryMailboxCache = null;
  inMemoryThreadCache = null;
  try {
    for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
      const key = window.localStorage.key(index);
      if (
        key?.startsWith(THREAD_STORAGE_PREFIX)
        || key?.startsWith(MAILBOX_STORAGE_PREFIX)
        || key?.startsWith(MAILBOX_SYNC_STORAGE_PREFIX)
      ) {
        window.localStorage.removeItem(key);
      }
    }
  } catch (_error) {}
}

function mailboxStorageKey(userKey: string): string {
  return `${MAILBOX_STORAGE_PREFIX}${userKey}`;
}

function mailboxSyncStorageKey(userKey: string): string {
  return `${MAILBOX_SYNC_STORAGE_PREFIX}${userKey}`;
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
