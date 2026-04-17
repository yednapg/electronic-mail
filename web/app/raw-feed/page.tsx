import type { SourceRecord } from '@decision-pipeline/types';

async function getRawFeed(): Promise<SourceRecord[]> {
  const response = await fetch('http://localhost:3001/raw-feed', {
    cache: 'no-store',
  });

  if (!response.ok) {
    throw new Error('Failed to fetch raw feed');
  }

  return response.json();
}

export default async function RawFeedPage() {
  const records = await getRawFeed();

  return (
    <main className="digest-page">
      <div className="digest-shell">
        <h1 className="digest-section-title">Raw Gmail Feed</h1>
        <p className="attention-copy">Records: {records.length}</p>

        <div className="digest-sections">
          {records.map((record) => (
            <section key={record.id} className="digest-section" aria-label={record.id}>
              <div className="digest-section-header">
                <h2 className="digest-section-title">
                  {typeof record.raw_payload.subject === 'string' && record.raw_payload.subject
                    ? record.raw_payload.subject
                    : 'Untitled'}
                </h2>
              </div>
              <p className="attention-copy">Source: {record.source}</p>
              <p className="attention-copy">Thread: {record.thread_id}</p>
              <p className="attention-copy">Received: {record.received_at}</p>
              <pre className="trace-block">{JSON.stringify(record.raw_payload, null, 2)}</pre>
            </section>
          ))}
        </div>
      </div>
    </main>
  );
}
