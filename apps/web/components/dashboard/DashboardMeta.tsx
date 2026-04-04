"use client";

import { useEffect, useState } from 'react';

import { formatClockTime, formatDate } from '../../lib/formatting';

type DashboardMetaProps = {
  dateLabel: string;
  timeLabel: string;
};

export function DashboardMeta({ dateLabel, timeLabel }: DashboardMetaProps) {
  const [currentLabels, setCurrentLabels] = useState({ dateLabel, timeLabel });

  useEffect(() => {
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
  }, []);

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
