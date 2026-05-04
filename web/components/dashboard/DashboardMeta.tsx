"use client";

/** Live-updating date and clock header for the dashboard. */
import { useEffect, useState } from 'react';

import { formatClockTime, formatDate } from '../../lib/formatting';

type DashboardMetaProps = {
  dateLabel: string;
  timeLabel: string;
  live?: boolean;
};

export function DashboardMeta({ dateLabel, timeLabel, live = true }: DashboardMetaProps) {
  const [currentLabels, setCurrentLabels] = useState({ dateLabel, timeLabel });

  useEffect(() => {
    if (!live) {
      setCurrentLabels({ dateLabel, timeLabel });
      return;
    }

    // The initial labels are server-rendered; refresh client-side to keep the clock ticking.
    const syncLabels = () => {
      const now = new Date();

      setCurrentLabels({
        dateLabel: formatDate(now),
        timeLabel: formatClockTime(now),
      });
    };

    syncLabels();

    const timer = setInterval(syncLabels, 1000);

    return () => clearInterval(timer);
  }, [dateLabel, live, timeLabel]);

  return (
    <header className="digest-meta" aria-label="Current date and time">
      <p className="digest-meta-label digest-meta-date" suppressHydrationWarning>
        {currentLabels.dateLabel}
      </p>
      <p className="digest-meta-label digest-meta-time" suppressHydrationWarning>
        {currentLabels.timeLabel}
      </p>
    </header>
  );
}
