'use client';

import { useEffect, useState } from 'react';

import type { AppSessionStateResponse } from './types';

const APP_SESSION_STORAGE_PREFIX = 'electronic-mail-app-session:v1:';
const CURRENT_USER_STORAGE_KEY = 'electronic-mail-current-user:v1';

let memorySession: AppSessionStateResponse | null = null;
let inFlightRefresh: Promise<AppSessionStateResponse | null> | null = null;
const listeners = new Set<() => void>();

export function useAppSession() {
  const [session, setSession] = useState<AppSessionStateResponse | null>(() => memorySession);
  const [refreshFailed, setRefreshFailed] = useState(false);

  useEffect(() => {
    function handleChange() {
      setSession(memorySession ?? readCurrentCachedAppSession());
    }
    listeners.add(handleChange);
    return () => {
      listeners.delete(handleChange);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (memorySession === null) {
      const cachedSession = readCurrentCachedAppSession();
      if (cachedSession !== null) {
        setSession(cachedSession);
      }
    }
    void refreshAppSession()
      .then((nextSession) => {
        if (!cancelled) {
          setSession(nextSession ?? memorySession ?? readCurrentCachedAppSession());
          setRefreshFailed(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setRefreshFailed(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { session, refreshFailed };
}

export async function warmAppSession(): Promise<AppSessionStateResponse | null> {
  return refreshAppSession();
}

export function getCachedAppSession(): AppSessionStateResponse | null {
  return memorySession ?? readCurrentCachedAppSession();
}

export async function refreshAppSession(): Promise<AppSessionStateResponse | null> {
  if (inFlightRefresh !== null) {
    return inFlightRefresh;
  }
  inFlightRefresh = fetchAppSession()
    .then((nextSession) => {
      const mergedSession = mergeAppSession(memorySession ?? readCurrentCachedAppSession(), nextSession);
      memorySession = mergedSession;
      writeCachedAppSession(mergedSession);
      notify();
      return mergedSession;
    })
    .finally(() => {
      inFlightRefresh = null;
    });
  return inFlightRefresh;
}

export function clearAppSessionCache(): void {
  memorySession = null;
  try {
    for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
      const key = window.localStorage.key(index);
      if (key?.startsWith(APP_SESSION_STORAGE_PREFIX)) {
        window.localStorage.removeItem(key);
      }
    }
  } catch (_error) {}
  notify();
}

function mergeAppSession(
  current: AppSessionStateResponse | null,
  next: AppSessionStateResponse,
): AppSessionStateResponse {
  if (current === null || current.user.id !== next.user.id) {
    return next;
  }

  const currentDashboardCount = dashboardItemCount(current);
  const nextDashboardCount = dashboardItemCount(next);
  const currentMailboxCount = current.mailbox.total_threads;
  const nextMailboxCount = next.mailbox.total_threads;

  return {
    ...next,
    dashboard: currentDashboardCount > 0 && nextDashboardCount === 0 ? current.dashboard : next.dashboard,
    mailbox: currentMailboxCount > 0 && nextMailboxCount === 0 ? current.mailbox : next.mailbox,
  };
}

function dashboardItemCount(session: AppSessionStateResponse): number {
  return (
    session.dashboard.feed.now.length
    + session.dashboard.feed.today.length
    + session.dashboard.feed.worth_knowing.length
  );
}

async function fetchAppSession(): Promise<AppSessionStateResponse> {
  const response = await fetch('/api/app/session', {
    cache: 'no-store',
    credentials: 'include',
    headers: {
      Accept: 'application/json',
    },
  });
  if (!response.ok) {
    throw new Error(`App session refresh failed (${response.status})`);
  }
  return response.json() as Promise<AppSessionStateResponse>;
}

function readCurrentCachedAppSession(): AppSessionStateResponse | null {
  try {
    if (typeof window === 'undefined') {
      return null;
    }
    const rawUserKey = window.localStorage.getItem(CURRENT_USER_STORAGE_KEY);
    if (rawUserKey === null) {
      return null;
    }
    const userKey = JSON.parse(rawUserKey) as unknown;
    return typeof userKey === 'string' ? readCachedAppSession(userKey) : null;
  } catch (_error) {
    return null;
  }
}

function readCachedAppSession(userKey: string): AppSessionStateResponse | null {
  try {
    const rawValue = window.localStorage.getItem(`${APP_SESSION_STORAGE_PREFIX}${userKey}`);
    if (rawValue === null) {
      return null;
    }
    const parsed = JSON.parse(rawValue) as Partial<AppSessionStateResponse>;
    if (parsed.user?.id !== userKey || parsed.dashboard === undefined || parsed.mailbox === undefined) {
      return null;
    }
    return parsed as AppSessionStateResponse;
  } catch (_error) {
    return null;
  }
}

function writeCachedAppSession(session: AppSessionStateResponse): void {
  try {
    window.localStorage.setItem(CURRENT_USER_STORAGE_KEY, JSON.stringify(session.user.id));
    window.localStorage.setItem(`${APP_SESSION_STORAGE_PREFIX}${session.user.id}`, JSON.stringify(session));
  } catch (_error) {}
}

function notify(): void {
  listeners.forEach((listener) => listener());
}
