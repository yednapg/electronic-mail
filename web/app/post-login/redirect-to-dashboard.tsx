'use client';

import { useEffect } from 'react';

import {
  DEMO_DASHBOARD_ROUTE,
  POST_LOGIN_MINIMUM_MS,
  POST_LOGIN_READY_TIMEOUT_MS,
} from '../../lib/demo-flow';

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

async function waitForDashboardReady(): Promise<void> {
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

export function RedirectToDashboard() {
  useEffect(() => {
    let cancelled = false;

    async function redirectWhenReady() {
      await Promise.all([
        wait(POST_LOGIN_MINIMUM_MS),
        Promise.race([
          waitForDashboardReady().catch(() => undefined),
          wait(POST_LOGIN_READY_TIMEOUT_MS),
        ]),
      ]);

      if (!cancelled) {
        window.location.replace(DEMO_DASHBOARD_ROUTE);
      }
    }

    void redirectWhenReady();

    return () => {
      cancelled = true;
    };
  }, []);

  return null;
}
