'use client';

import { useEffect } from 'react';

import {
  DASHBOARD_ROUTE,
  POST_LOGIN_MINIMUM_MS,
} from '../../lib/post-login-flow';
import { waitForFirstRunImportReady } from '../../lib/first-run-import';

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

export function RedirectToDashboard() {
  useEffect(() => {
    const controller = new AbortController();

    function redirectToDashboard() {
      window.location.assign(DASHBOARD_ROUTE);
    }

    async function redirectWhenReady() {
      await Promise.all([
        wait(POST_LOGIN_MINIMUM_MS),
        waitForFirstRunImportReady({ signal: controller.signal }),
      ]);

      redirectToDashboard();
    }

    void redirectWhenReady().catch(() => undefined);

    return () => {
      controller.abort();
    };
  }, []);

  return null;
}
