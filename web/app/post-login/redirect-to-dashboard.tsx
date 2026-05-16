'use client';

import { useEffect } from 'react';

import { DASHBOARD_ROUTE } from '../../lib/post-login-flow';
import { warmPostLoginCaches } from '../../lib/post-login-cache';
import { waitForPostLoginReady } from '../../lib/post-login-readiness';

const RETURNING_MINIMUM_MS = 30000;
const FIRST_TIME_MINIMUM_MS = 60000;

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
      const startedAt = Date.now();
      let minimumMs = FIRST_TIME_MINIMUM_MS;
      await waitForPostLoginReady({
        signal: controller.signal,
        onUpdate: (readiness) => {
          minimumMs = readiness.mode === 'returning' ? RETURNING_MINIMUM_MS : FIRST_TIME_MINIMUM_MS;
        },
      });
      const warmCaches = warmPostLoginCaches().catch(() => undefined);
      const remainingMs = minimumMs - (Date.now() - startedAt);
      if (remainingMs > 0) {
        await Promise.all([wait(remainingMs), warmCaches]);
      } else {
        await warmCaches;
      }

      redirectToDashboard();
    }

    void redirectWhenReady().catch(() => undefined);

    return () => {
      controller.abort();
    };
  }, []);

  return null;
}
