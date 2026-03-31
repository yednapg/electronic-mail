import { useEffect, useState } from 'react';

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:3001';

type HealthResponse = {
  status: 'ok';
};

function App() {
  const [health, setHealth] = useState<string>('loading');

  useEffect(() => {
    async function loadHealth(): Promise<void> {
      try {
        const response = await fetch(`${apiBaseUrl}/health`);
        const data = (await response.json()) as HealthResponse;

        setHealth(data.status);
      } catch {
        setHealth('unavailable');
      }
    }

    void loadHealth();
  }, []);

  return (
    <main className="app-shell">
      <section className="status-card">
        <p className="eyebrow">Decision Pipeline System</p>
        <h1>Frontend is running</h1>
        <p>API health: {health}</p>
      </section>
    </main>
  );
}

export default App;
