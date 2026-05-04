import type { SourceRecord } from '@electronic-mail/types';

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
  let records: SourceRecord[] = [];
  let errorMessage: string | null = null;

  try {
    records = await getRawFeed();
  } catch (error) {
    errorMessage = error instanceof Error ? error.message : 'Raw feed is unavailable.';
  }

  return (
    <main className="digest-page">
      <div className="debug-shell">
        <header className="debug-header">
          <h1 className="debug-title">Raw Gmail Feed</h1>
          <p className="debug-copy">{errorMessage ?? `Records: ${records.length}`}</p>
        </header>

        {errorMessage === null ? (
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
      </div>
    </main>
  );
}
