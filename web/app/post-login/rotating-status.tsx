'use client';

import { useEffect, useRef, useState } from 'react';

import {
  DEMO_DASHBOARD_ROUTE,
  POST_LOGIN_MINIMUM_MS,
  POST_LOGIN_READY_TIMEOUT_MS,
} from '../../lib/demo-flow';
import { formatDashboardImportStatus, waitForDashboardImportJob } from '../../lib/dashboard-import';

const STATUS_VISIBLE_MS = 1600;
const STATUS_FADE_MS = 260;

const statusMessages = [
  'Reading latest Gmail threads...',
  'Grouping orders, bills, bugs, refunds, and approvals...',
  'Finding current state and next move...',
  'Keeping raw emails as evidence...',
];

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

async function waitForDashboardReady(onStatus?: (message: string) => void): Promise<void> {
  if (process.env.NEXT_PUBLIC_DEMO_MODE !== 'true') {
    await waitForDashboardImportJob({
      timeoutMs: null,
      onUpdate: (job) => onStatus?.(formatDashboardImportStatus(job)),
    });
    return;
  }

  const response = await fetch(DEMO_DASHBOARD_ROUTE, {
    cache: 'no-store',
    credentials: 'same-origin',
    headers: {
      Accept: 'text/html',
    },
  });

  if (!response.ok) {
    throw new Error('Dashboard is not ready yet.');
  }
}

export function RotatingStatus() {
  const [activeIndex, setActiveIndex] = useState(0);
  const [liveStatus, setLiveStatus] = useState<string | null>(null);
  const [stopped, setStopped] = useState(false);
  const [visible, setVisible] = useState(true);
  const swapTimeoutRef = useRef<number | null>(null);
  const activeMessage = liveStatus ?? statusMessages[activeIndex];

  useEffect(() => {
    const interval = window.setInterval(() => {
      if (stopped) {
        return;
      }

      setVisible(false);

      swapTimeoutRef.current = window.setTimeout(() => {
        setActiveIndex((index) => (index + 1) % statusMessages.length);
        setVisible(true);
      }, STATUS_FADE_MS);
    }, STATUS_VISIBLE_MS + STATUS_FADE_MS);

    return () => {
      window.clearInterval(interval);
      if (swapTimeoutRef.current !== null) {
        window.clearTimeout(swapTimeoutRef.current);
      }
    };
  }, [stopped]);

  useEffect(() => {
    let cancelled = false;

    function redirectToDashboard() {
      window.location.assign(DEMO_DASHBOARD_ROUTE);
    }

    async function redirectWhenReady() {
      if (process.env.NEXT_PUBLIC_DEMO_MODE !== 'true') {
        await Promise.all([
          wait(POST_LOGIN_MINIMUM_MS),
          waitForDashboardReady(setLiveStatus),
        ]);

        if (!cancelled) {
          redirectToDashboard();
        }
        return;
      }

      const fallbackTimer = window.setTimeout(() => {
        redirectToDashboard();
      }, POST_LOGIN_READY_TIMEOUT_MS);

      await Promise.all([
        wait(POST_LOGIN_MINIMUM_MS),
        Promise.race([
          waitForDashboardReady().catch(() => undefined),
          wait(POST_LOGIN_READY_TIMEOUT_MS),
        ]),
      ]);

      window.clearTimeout(fallbackTimer);
      redirectToDashboard();
    }

    void redirectWhenReady().catch(async (error) => {
      await wait(POST_LOGIN_MINIMUM_MS);
      if (!cancelled) {
        setStopped(true);
        setVisible(true);
        setLiveStatus(error instanceof Error ? error.message : 'Dashboard preparation failed. Please try again.');
      }
    });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <p className="post-login-rotating-status" aria-live="polite" aria-label={activeMessage}>
      <span
        className={`post-login-status-text ${visible ? 'post-login-status-text-visible' : ''}`}
        aria-hidden="true"
      >
        {activeMessage}
      </span>
    </p>
  );
}
