'use client';

import { useEffect, useRef } from 'react';

import { refreshAppSession } from './app-session-store';

const ACTIVE_SYNC_INTERVAL_MS = 30_000;
const MIN_SYNC_GAP_MS = 12_000;
const REFRESH_DELAYS_MS = [2_000, 6_000, 12_000];

export function useActiveMailboxSync(enabled: boolean) {
  const lastSyncAtRef = useRef(0);
  const refreshTimersRef = useRef<number[]>([]);

  useEffect(() => {
    if (!enabled) {
      return;
    }

    let stopped = false;

    const clearRefreshTimers = () => {
      refreshTimersRef.current.forEach((timerId) => window.clearTimeout(timerId));
      refreshTimersRef.current = [];
    };

    const scheduleSessionRefresh = () => {
      clearRefreshTimers();
      refreshTimersRef.current = REFRESH_DELAYS_MS.map((delay) => (
        window.setTimeout(() => {
          void refreshAppSession().catch(() => {});
        }, delay)
      ));
    };

    const triggerSync = () => {
      if (stopped || document.visibilityState === 'hidden') {
        return;
      }
      const now = Date.now();
      if (now - lastSyncAtRef.current < MIN_SYNC_GAP_MS) {
        return;
      }
      lastSyncAtRef.current = now;
      void fetch('/api/mailbox/sync', {
        method: 'POST',
        cache: 'no-store',
        credentials: 'include',
        headers: {
          Accept: 'application/json',
        },
      })
        .then((response) => {
          if (response.ok) {
            scheduleSessionRefresh();
          }
        })
        .catch(() => {});
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        triggerSync();
      }
    };

    triggerSync();
    const intervalId = window.setInterval(triggerSync, ACTIVE_SYNC_INTERVAL_MS);
    window.addEventListener('focus', triggerSync);
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      stopped = true;
      window.clearInterval(intervalId);
      window.removeEventListener('focus', triggerSync);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      clearRefreshTimers();
    };
  }, [enabled]);
}
