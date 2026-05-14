'use client';

import { useEffect, useRef, useState } from 'react';

import {
  DASHBOARD_ROUTE,
  POST_LOGIN_MINIMUM_MS,
} from '../../lib/post-login-flow';
import { waitForFirstRunImportReady } from '../../lib/first-run-import';

const STATUS_VISIBLE_MS = 6000;
const LONG_WAIT_MS = 90000;

const statusMessages = [
  'Importing emails ...',
  'Understanding threads ...',
  'Writing titles and summaries ...',
  'Finding what needs action ...',
  'Building dashboard ...',
  'Almost ready!',
];

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

export function RotatingStatus() {
  const [activeIndex, setActiveIndex] = useState(0);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLongWait, setIsLongWait] = useState(false);
  const intervalRef = useRef<number | null>(null);
  const activeMessage = errorMessage ?? (isLongWait ? 'Still working, keep this open ...' : statusMessages[activeIndex]);

  useEffect(() => {
    intervalRef.current = window.setInterval(() => {
      setActiveIndex((index) => {
        if (index >= statusMessages.length - 1) {
          if (intervalRef.current !== null) {
            window.clearInterval(intervalRef.current);
            intervalRef.current = null;
          }
          return index;
        }
        return index + 1;
      });
    }, STATUS_VISIBLE_MS);

    return () => {
      if (intervalRef.current !== null) {
        window.clearInterval(intervalRef.current);
      }
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    function redirectToDashboard() {
      window.location.assign(DASHBOARD_ROUTE);
    }

    async function redirectWhenReady() {
      await Promise.all([
        wait(POST_LOGIN_MINIMUM_MS),
        waitForFirstRunImportReady({ signal: controller.signal }),
      ]);

      if (!cancelled) {
        redirectToDashboard();
      }
    }

    void redirectWhenReady().catch(async (error) => {
      await wait(POST_LOGIN_MINIMUM_MS);
      if (!cancelled) {
        setErrorMessage(error instanceof Error ? error.message : 'Setup failed. Please refresh.');
      }
    });

    const longWaitTimeout = window.setTimeout(() => {
      if (!cancelled) {
        setIsLongWait(true);
      }
    }, LONG_WAIT_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(longWaitTimeout);
      controller.abort();
    };
  }, []);

  return (
    <p className="post-login-rotating-status" aria-live="polite" aria-label={activeMessage}>
      <span
        key={activeMessage}
        className="post-login-status-text post-login-status-text-visible"
        aria-hidden="true"
      >
        {activeMessage.split('').map((character, index) => (
          <span
            // The text is static per animation state, so index is stable here.
            key={`${character}-${index}`}
            className="post-login-status-letter"
            style={{ '--letter-index': index } as React.CSSProperties}
          >
            {character === ' ' ? '\u00a0' : character}
          </span>
        ))}
      </span>
    </p>
  );
}
