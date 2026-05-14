import type { SourceRecord } from '@decision-pipeline/types';
import { redirect } from 'next/navigation';

import { getBackendURL, getDashboard } from '../../lib/api';
import { getServerCookieHeader } from '../../lib/server-cookies';

type RawFeedPageProps = {
  searchParams?: Promise<{
    item?: string;
  }>;
};

async function getRawFeed(cookie: string | null): Promise<SourceRecord[]> {
  const response = await fetch(`${getBackendURL()}/raw-feed`, {
    cache: 'no-store',
    headers: cookie === null ? undefined : { Cookie: cookie },
  });

  if (!response.ok) {
    throw new Error('Failed to fetch raw feed');
  }

  return response.json();
}

export default async function RawFeedPage({ searchParams }: RawFeedPageProps) {
  const cookie = await getServerCookieHeader();
  const [params, dashboard] = await Promise.all([searchParams, getDashboard({ cookie })]);
  if (!dashboard.auth.connected) {
    redirect('/');
  }

  const itemId = params?.item;
  let records: SourceRecord[] = [];
  let errorMessage: string | null = null;

  try {
    records = await getRawFeed(cookie);
  } catch (error) {
    errorMessage = error instanceof Error ? error.message : 'Raw feed is unavailable.';
  }

  return (
    <main className="digest-page">
      <div className="debug-shell">
        <header className="debug-header">
          <h1 className="debug-title">{itemId === undefined ? 'Raw Gmail Feed' : 'Raw Gmail Evidence'}</h1>
          <p className="debug-copy">
            {errorMessage ?? `${itemId === undefined ? 'Records' : 'Matching records'}: ${records.length}`}
          </p>
        </header>

        {errorMessage === null && records.length > 0 ? (
          <div className="debug-sections">
            {records.map((record) => (
              <section key={record.id} className="digest-section" aria-label={record.id}>
                <div className="debug-section-header">
                  <h2 className="debug-section-title">
                    {typeof record.raw_payload.subject === 'string' && record.raw_payload.subject
                      ? record.raw_payload.subject
                      : 'Untitled'}
                  </h2>
                </div>
                <div className="debug-meta">
                  <p className="debug-copy">Source: {record.source}</p>
                  <p className="debug-copy">Thread: {record.thread_id}</p>
                  <p className="debug-copy">Received: {record.received_at}</p>
                </div>
                <pre className="trace-block">{JSON.stringify(record.raw_payload, null, 2)}</pre>
              </section>
            ))}
          </div>
        ) : null}

        {errorMessage === null && records.length === 0 ? (
          <p className="debug-copy">No matching Gmail records.</p>
        ) : null}
      </div>
    </main>
  );
}
