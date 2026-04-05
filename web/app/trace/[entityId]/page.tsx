import type { TraceReplayResponse } from '@decision-pipeline/types';

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
  const trace = await getTrace(entityId);

  return (
    <main className="digest-page">
      <div className="digest-shell">
        <h1 className="digest-section-title">Trace Replay</h1>
        <p className="attention-copy">Entity: {trace.entity_id}</p>
        <p className="attention-copy">Source records: {trace.source_record_ids.join(', ') || 'None'}</p>

        <div className="digest-sections">
          {trace.items.map((item) => (
            <section key={item.id} className="digest-section" aria-label={item.stage}>
              <div className="digest-section-header">
                <h2 className="digest-section-title">{item.stage}</h2>
              </div>
              <p className="attention-copy">{item.created_at}</p>
              <pre className="trace-block">{JSON.stringify({ input: item.input, output: item.output }, null, 2)}</pre>
            </section>
          ))}
        </div>
      </div>
    </main>
  );
}
