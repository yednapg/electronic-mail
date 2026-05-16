'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useMemo } from 'react';

import { SignedInAppChrome } from '../../components/app/AppChrome';
import { DashboardView } from '../../components/dashboard/DashboardView';
import { useAppSession } from '../../lib/app-session-store';
import { useActiveMailboxSync } from '../../lib/use-active-mailbox-sync';
import {
  buildAgenda,
  buildSections,
  buildSummary,
} from '../../lib/dashboard-view-model';
import { formatClockTime, formatDate } from '../../lib/formatting';
import type { DashboardResponse } from '../../lib/types';

type DashboardClientProps = {
  initialDashboard?: DashboardResponse | null;
};

export function DashboardClient({ initialDashboard = null }: DashboardClientProps) {
  const router = useRouter();
  const { session, refreshFailed } = useAppSession();
  const now = useMemo(() => new Date(), []);
  const dashboard = session?.dashboard ?? initialDashboard;

  useActiveMailboxSync(Boolean(session?.dashboard.auth.connected));

  useEffect(() => {
    router.prefetch('/gmail');
  }, [router]);

  useEffect(() => {
    if (session !== null && !session.dashboard.auth.connected) {
      router.replace('/');
    }
    if (session === null && refreshFailed) {
      router.replace('/');
    }
  }, [refreshFailed, router, session]);

  return (
    <SignedInAppChrome active="dashboard">
      {dashboard === null ? (
        <DashboardLoadingView dateLabel={formatDate(now)} timeLabel={formatClockTime(now)} />
      ) : (
        <DashboardView
          dateLabel={formatDate(now)}
          timeLabel={formatClockTime(now)}
          liveMeta
          summary={buildSummary(dashboard)}
          agenda={buildAgenda(dashboard.feed)}
          sections={buildSections(dashboard.feed)}
        />
      )}
      {session?.mailbox.full_import_running ? (
        <p className="inbox-refresh-status" role="status">
          Processing older mail in background.
        </p>
      ) : null}
      {refreshFailed ? (
        <p className="inbox-refresh-status" role="status">
          Dashboard could not refresh. Showing last saved state.
        </p>
      ) : null}
    </SignedInAppChrome>
  );
}

function DashboardLoadingView({ dateLabel, timeLabel }: { dateLabel: string; timeLabel: string }) {
  return (
    <DashboardView
      dateLabel={dateLabel}
      timeLabel={timeLabel}
      liveMeta={false}
      summary={{ headline: 'Dashboard', brief: 'Loading your saved workspace...' }}
      agenda={[]}
      sections={[
        { id: 'now', title: 'Now', items: [], maxVisible: 6, collapsedByDefault: true },
        { id: 'today', title: 'Today', items: [], maxVisible: 5, collapsedByDefault: true },
        { id: 'worth-knowing', title: 'Worth Knowing', items: [], maxVisible: 3, collapsedByDefault: true },
      ]}
    />
  );
}
