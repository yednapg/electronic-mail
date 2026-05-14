'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';

import { SignedInAppChrome } from '../../components/app/AppChrome';
import { DashboardView } from '../../components/dashboard/DashboardView';
import {
  buildAgenda,
  buildSections,
  buildSummary,
} from '../../lib/dashboard-view-model';
import {
  formatDashboardImportStatus,
  waitForDashboardImportJob,
} from '../../lib/dashboard-import';
import { formatClockTime, formatDate } from '../../lib/formatting';
import type { AuthMeResponse, DashboardResponse } from '../../lib/types';

const DASHBOARD_STORAGE_PREFIX = 'electronic-mail-dashboard:v2:';
const DEFAULT_BROWSER_BACKEND_URL = 'http://localhost:3001';

let inMemoryDashboardCache: { userKey: string; dashboard: DashboardResponse } | null = null;

export function DashboardClient() {
  const router = useRouter();
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(() => inMemoryDashboardCache?.dashboard ?? null);
  const [importStatus, setImportStatus] = useState<string | null>(null);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const now = useMemo(() => new Date(), []);

  useEffect(() => {
    router.prefetch('/gmail');
  }, [router]);

  useEffect(() => {
    let cancelled = false;

    void fetchAuthMe()
      .then((me) => {
        if (cancelled) {
          return;
        }
        if (!me.authenticated || me.user === undefined || me.user === null) {
          clearDashboardCaches();
          router.replace('/');
          return;
        }

        const userId = me.user.id;
        const cachedDashboard = inMemoryDashboardCache?.userKey === userId
          ? inMemoryDashboardCache.dashboard
          : readCachedDashboard(userId);
        if (cachedDashboard !== null) {
          setDashboard(cachedDashboard);
        } else if (inMemoryDashboardCache?.userKey !== userId) {
          setDashboard(null);
        }

        return fetchDashboard()
          .then((nextDashboard) => {
            if (cancelled || nextDashboard === null) {
              return;
            }
            if (!nextDashboard.auth.connected) {
              clearDashboardCaches();
              router.replace('/');
              return;
            }
            setDashboard(nextDashboard);
            setRefreshFailed(false);
            inMemoryDashboardCache = { userKey: userId, dashboard: nextDashboard };
            writeCachedDashboard(userId, nextDashboard);

            if (shouldPrepareDashboard(nextDashboard)) {
              setImportStatus('Preparing your dashboard from Gmail...');
              void waitForDashboardImportJob({
                timeoutMs: null,
                onUpdate: (job) => {
                  if (!cancelled) {
                    setImportStatus(formatDashboardImportStatus(job));
                  }
                },
              })
                .then(() => fetchDashboard())
                .then((preparedDashboard) => {
                  if (cancelled || preparedDashboard === null) {
                    return;
                  }
                  setDashboard(preparedDashboard);
                  inMemoryDashboardCache = { userKey: userId, dashboard: preparedDashboard };
                  writeCachedDashboard(userId, preparedDashboard);
                  setImportStatus(null);
                })
                .catch((error) => {
                  if (!cancelled) {
                    setImportStatus(error instanceof Error ? error.message : 'Dashboard preparation failed.');
                  }
                });
            } else {
              setImportStatus(null);
            }
          });
      })
      .catch(() => {
        if (!cancelled) {
          setRefreshFailed(true);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [router]);

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
      {refreshFailed ? (
        <p className="inbox-refresh-status" role="status">
          Dashboard could not refresh.
        </p>
      ) : null}
      {importStatus ? (
        <p className="inbox-refresh-status" role="status">
          {importStatus}
        </p>
      ) : null}
    </SignedInAppChrome>
  );
}

function shouldPrepareDashboard(dashboard: DashboardResponse): boolean {
  if (!dashboard.auth.connected) {
    return false;
  }

  return (
    dashboard.feed.now.length === 0
    && dashboard.feed.today.length === 0
    && dashboard.feed.worth_knowing.length === 0
  );
}

function DashboardLoadingView({ dateLabel, timeLabel }: { dateLabel: string; timeLabel: string }) {
  return (
    <DashboardView
      dateLabel={dateLabel}
      timeLabel={timeLabel}
      liveMeta={false}
      summary={{ headline: 'Dashboard', brief: 'Refreshing your work cache...' }}
      agenda={[]}
      sections={[
        { id: 'now', title: 'Now', items: [], maxVisible: 6, collapsedByDefault: true },
        { id: 'today', title: 'Today', items: [], maxVisible: 5, collapsedByDefault: true },
        { id: 'worth-knowing', title: 'Worth Knowing', items: [], maxVisible: 3, collapsedByDefault: true },
      ]}
    />
  );
}

async function fetchAuthMe(): Promise<AuthMeResponse> {
  const response = await fetch(`${getBrowserBackendURL()}/v1/auth/me`, {
    cache: 'no-store',
    credentials: 'include',
    headers: {
      Accept: 'application/json',
    },
  });
  if (!response.ok) {
    throw new Error('Session could not refresh.');
  }
  return response.json() as Promise<AuthMeResponse>;
}

async function fetchDashboard(): Promise<DashboardResponse | null> {
  const urls = [
    `${getBrowserBackendURL()}/dashboard`,
    '/api/dashboard',
  ];

  let lastError: unknown = null;
  for (const url of urls) {
    try {
      const response = await fetch(url, {
        cache: 'no-store',
        credentials: 'include',
        headers: {
          Accept: 'application/json',
        },
      });
      if (response.status === 401) {
        return null;
      }
      if (!response.ok) {
        throw new Error('Dashboard refresh failed.');
      }
      return response.json() as Promise<DashboardResponse>;
    } catch (error) {
      lastError = error;
    }
  }

  throw lastError instanceof Error ? lastError : new Error('Dashboard refresh failed.');
}

function readCachedDashboard(userKey: string): DashboardResponse | null {
  try {
    const cached = window.localStorage.getItem(dashboardStorageKey(userKey));
    if (cached === null) {
      return null;
    }

    const parsed = JSON.parse(cached) as Partial<DashboardResponse>;
    if (parsed.auth === undefined || parsed.feed === undefined) {
      return null;
    }

    return parsed as DashboardResponse;
  } catch (_error) {
    return null;
  }
}

function writeCachedDashboard(userKey: string, dashboard: DashboardResponse) {
  try {
    window.localStorage.setItem(dashboardStorageKey(userKey), JSON.stringify(dashboard));
  } catch (_error) {}
}

function clearDashboardCaches() {
  inMemoryDashboardCache = null;
  try {
    for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
      const key = window.localStorage.key(index);
      if (key?.startsWith(DASHBOARD_STORAGE_PREFIX)) {
        window.localStorage.removeItem(key);
      }
    }
  } catch (_error) {}
}

function dashboardStorageKey(userKey: string): string {
  return `${DASHBOARD_STORAGE_PREFIX}${userKey}`;
}

function getBrowserBackendURL(): string {
  return (process.env.NEXT_PUBLIC_ELECTRONIC_MAIL_BACKEND_URL ?? DEFAULT_BROWSER_BACKEND_URL).replace(/\/+$/, '');
}
