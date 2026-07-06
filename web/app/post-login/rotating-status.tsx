'use client';

import { useEffect, useRef, useState } from 'react';

import { DASHBOARD_ROUTE } from '../../lib/post-login-flow';
import { warmPostLoginCaches } from '../../lib/post-login-cache';
import { waitForPostLoginReady } from '../../lib/post-login-readiness';
import type { PostLoginReadinessResponse } from '../../lib/types';

const STATUS_VISIBLE_MS = 6000;
const LONG_WAIT_MS = 90000;
const RETURNING_MINIMUM_MS = 30000;
const FIRST_TIME_MINIMUM_MS = 60000;

const firstTimeStatusMessages = [
  'Importing emails ...',
  'Loading your inbox ...',
  'Preparing Gmail ...',
  'Checking for new mail ...',
  'Preparing your inbox ...',
  'Almost ready!',
];

const returningStatusMessages = [
  'Welcome back ...',
  'Checking your latest Gmail ...',
  'Refreshing your inbox ...',
  'Almost ready!',
];

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

export function RotatingStatus() {
  const [activeIndex, setActiveIndex] = useState(0);
  const [readiness, setReadiness] = useState<PostLoginReadinessResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isLongWait, setIsLongWait] = useState(false);
  const intervalRef = useRef<number | null>(null);
  const messages = readiness?.mode === 'returning' ? returningStatusMessages : firstTimeStatusMessages;
  const activeMessage = errorMessage ?? (isLongWait ? 'Still setting things up, keep this open ...' : statusMessage(messages, activeIndex, readiness));

  useEffect(() => {
    intervalRef.current = window.setInterval(() => {
      setActiveIndex((index) => {
        if (index >= Math.max(firstTimeStatusMessages.length, returningStatusMessages.length) - 1) {
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
      const startedAt = Date.now();
      let minimumMs = FIRST_TIME_MINIMUM_MS;
      const ready = await waitForPostLoginReady({
        signal: controller.signal,
        onUpdate: (nextReadiness) => {
          if (!cancelled) {
            setReadiness(nextReadiness);
          }
          minimumMs = nextReadiness.mode === 'returning' ? RETURNING_MINIMUM_MS : FIRST_TIME_MINIMUM_MS;
        },
      });
      if (!cancelled) {
        setReadiness(ready);
      }
      const warmCaches = warmPostLoginCaches().catch(() => undefined);
      const remainingMs = minimumMs - (Date.now() - startedAt);
      if (remainingMs > 0) {
        await Promise.all([wait(remainingMs), warmCaches]);
      } else {
        await warmCaches;
      }

      if (!cancelled) {
        redirectToDashboard();
      }
    }

    void redirectWhenReady().catch(async (error) => {
      await wait(3000);
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

function statusMessage(messages: string[], activeIndex: number, readiness: PostLoginReadinessResponse | null): string {
  const index = Math.min(activeIndex, messages.length - 1);
  if (readiness?.mode === 'returning' && index === 0) {
    return `Welcome back${firstNameSuffix(readiness.user_display_name)}!`;
  }
  return messages[index] ?? firstTimeStatusMessages[0];
}

function firstNameSuffix(value: string | null | undefined): string {
  if (!value) {
    return '';
  }
  const name = value.split('@', 1)[0].replace(/[._-]+/g, ' ').trim().split(/\s+/)[0];
  return name ? `, ${name.charAt(0).toUpperCase()}${name.slice(1)}` : '';
}
