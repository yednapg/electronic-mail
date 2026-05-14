import type { TraceReplayResponse } from '@electronic-mail/types';
import { redirect } from 'next/navigation';

import { getBackendURL, getDashboard } from '../../../lib/api';
import { getServerCookieHeader } from '../../../lib/server-cookies';

type TracePageProps = {
  params: Promise<{
    entityId: string;
  }>;
};

async function getTrace(entityId: string, cookie: string | null): Promise<TraceReplayResponse> {
  const response = await fetch(`${getBackendURL()}/trace/${encodeURIComponent(entityId)}`, {
    cache: 'no-store',
    headers: cookie === null ? undefined : { Cookie: cookie },
  });

  if (!response.ok) {
    throw new Error('Failed to fetch trace');
  }

  return response.json();
}

export default async function TracePage({ params }: TracePageProps) {
  const cookie = await getServerCookieHeader();
  const [{ entityId }, dashboard] = await Promise.all([params, getDashboard({ cookie })]);
  if (!dashboard.auth.connected) {
    redirect('/');
  }

  let trace: TraceReplayResponse | null = null;
  let errorMessage: string | null = null;

  try {
    trace = await getTrace(entityId, cookie);
  } catch (error) {
    errorMessage = error instanceof Error ? error.message : 'Trace replay is unavailable.';
  }

  return (
    <main className="digest-page">
      <div className="debug-shell">
        <header className="debug-header">
          <h1 className="debug-title">Trace Replay</h1>
          <div className="debug-meta">
            <p className="debug-copy">Entity: {trace?.entity_id ?? entityId}</p>
            <p className="debug-copy">
              {errorMessage ?? `Source records: ${trace?.source_record_ids.join(', ') || 'None'}`}
            </p>
          </div>
        </header>

        {trace ? (
          <div className="debug-sections">
            {trace.items.map((item) => (
              <section key={item.id} className="digest-section" aria-label={item.stage}>
                <div className="debug-section-header">
                  <h2 className="debug-section-title">{item.stage}</h2>
                </div>
                <p className="debug-copy">{item.created_at}</p>
                <pre className="trace-block">{JSON.stringify({ input: item.input, output: item.output }, null, 2)}</pre>
              </section>
            ))}
          </div>
        ) : null}
      </div>
    </main>
  );
}
