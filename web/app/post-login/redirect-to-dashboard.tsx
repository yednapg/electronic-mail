'use client';

import { useEffect } from 'react';

import {
  DEMO_DASHBOARD_ROUTE,
  POST_LOGIN_MINIMUM_MS,
  POST_LOGIN_READY_TIMEOUT_MS,
} from '../../lib/demo-flow';
import { waitForDashboardImportJob } from '../../lib/dashboard-import';

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

async function waitForDashboardReady(): Promise<void> {
  if (process.env.NEXT_PUBLIC_DEMO_MODE !== 'true') {
    await waitForDashboardImportJob({
      timeoutMs: POST_LOGIN_READY_TIMEOUT_MS,
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

export function RedirectToDashboard() {
  useEffect(() => {
    function redirectToDashboard() {
      window.location.assign(DEMO_DASHBOARD_ROUTE);
    }

    const fallbackTimer = window.setTimeout(() => {
      redirectToDashboard();
    }, POST_LOGIN_READY_TIMEOUT_MS);

    async function redirectWhenReady() {
      if (process.env.NEXT_PUBLIC_DEMO_MODE !== 'true') {
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

  return null;
}
