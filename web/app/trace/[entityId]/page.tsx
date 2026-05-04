import type { TraceReplayResponse } from '@decision-pipeline/types';

import { isDemoMode } from '../../../lib/api';
import { getDemoTrace } from '../../../lib/demo-evidence';

type TracePageProps = {
  params: Promise<{
    entityId: string;
  }>;
};

async function getTrace(entityId: string): Promise<TraceReplayResponse> {
  const response = await fetch(`http://localhost:3001/trace/${entityId}`, {
    cache: 'no-store',
  });

  if (!response.ok) {
    throw new Error('Failed to fetch trace');
  }

  return response.json();
}

export default async function TracePage({ params }: TracePageProps) {
  const { entityId } = await params;
  let trace: TraceReplayResponse | null = null;
  let errorMessage: string | null = null;

  if (isDemoMode()) {
    trace = getDemoTrace(entityId);
    errorMessage = trace === null ? 'No demo trace exists for this entity.' : null;
  } else {
    try {
      trace = await getTrace(entityId);
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : 'Trace replay is unavailable.';
    }
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
