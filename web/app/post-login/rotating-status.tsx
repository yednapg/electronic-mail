'use client';

import { useEffect, useRef, useState } from 'react';

import {
  DEMO_DASHBOARD_ROUTE,
  POST_LOGIN_MINIMUM_MS,
  POST_LOGIN_READY_TIMEOUT_MS,
} from '../../lib/demo-flow';

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

async function waitForDashboardReady(): Promise<void> {
  if (process.env.NEXT_PUBLIC_DEMO_MODE === 'false') {
    const response = await fetch('/api/dashboard/prepare', {
      method: 'POST',
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
      },
    });

    if (!response.ok) {
      throw new Error('Dashboard preparation failed.');
    }
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
  const [visible, setVisible] = useState(true);
  const swapTimeoutRef = useRef<number | null>(null);

  useEffect(() => {
    const interval = window.setInterval(() => {
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
  }, []);

  useEffect(() => {
    function redirectToDashboard() {
      window.location.assign(DEMO_DASHBOARD_ROUTE);
    }

    const fallbackTimer = window.setTimeout(() => {
      redirectToDashboard();
    }, POST_LOGIN_READY_TIMEOUT_MS);

    async function redirectWhenReady() {
      if (process.env.NEXT_PUBLIC_DEMO_MODE === 'false') {
        await Promise.all([
          wait(POST_LOGIN_MINIMUM_MS),
          waitForDashboardReady(),
        ]);

        redirectToDashboard();
        return;
      }

      await Promise.all([
        wait(POST_LOGIN_MINIMUM_MS),
        Promise.race([
          waitForDashboardReady().catch(() => undefined),
          wait(POST_LOGIN_READY_TIMEOUT_MS),
        ]),
      ]);

      redirectToDashboard();
    }

    void redirectWhenReady().catch(async () => {
      await wait(POST_LOGIN_MINIMUM_MS);
      redirectToDashboard();
    });

    return () => {
      window.clearTimeout(fallbackTimer);
    };
  }, []);

  return (
    <p className="post-login-rotating-status" aria-live="polite" aria-label={statusMessages[activeIndex]}>
      <span
        className={`post-login-status-text ${visible ? 'post-login-status-text-visible' : ''}`}
        aria-hidden="true"
      >
        {statusMessages[activeIndex]}
      </span>
    </p>
  );
}
