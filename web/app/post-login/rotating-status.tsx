'use client';

import { useEffect, useRef, useState } from 'react';

const STATUS_VISIBLE_MS = 1600;
const STATUS_FADE_MS = 260;

const statusMessages = [
  'Checking Gmail and Calendar...',
  'Finding replies, tasks, bills, and invites...',
  "Turning it into today's list...",
  'See demo.',
];

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
