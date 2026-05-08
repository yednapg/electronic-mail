import type { DashboardImportJobResponse } from '@decision-pipeline/types';

type Fetcher = typeof fetch;

const IMPORT_JOB_POLL_MS = 1000;

export class DashboardImportError extends Error {
  constructor(message: string, readonly job?: DashboardImportJobResponse) {
    super(message);
    this.name = 'DashboardImportError';
  }
}

export async function createDashboardImportJob(fetcher: Fetcher = fetch): Promise<DashboardImportJobResponse> {
  const response = await fetcher('/api/dashboard/import-jobs', {
    method: 'POST',
    cache: 'no-store',
    headers: {
      Accept: 'application/json',
    },
  });

  if (!response.ok) {
    throw new DashboardImportError('Dashboard import job could not be started.');
  }

  return response.json();
}

export async function getDashboardImportJob(
  jobId: string,
  fetcher: Fetcher = fetch,
): Promise<DashboardImportJobResponse> {
  const response = await fetcher(`/api/dashboard/import-jobs/${encodeURIComponent(jobId)}`, {
    cache: 'no-store',
    headers: {
      Accept: 'application/json',
    },
  });

  if (!response.ok) {
    throw new DashboardImportError('Dashboard import job status could not be read.');
  }

  return response.json();
}

export async function waitForDashboardImportJob({
  fetcher = fetch,
  timeoutMs,
  pollMs = IMPORT_JOB_POLL_MS,
}: {
  fetcher?: Fetcher;
  timeoutMs: number;
  pollMs?: number;
}): Promise<DashboardImportJobResponse> {
  const created = await createDashboardImportJob(fetcher);
  const startedAt = Date.now();
  let latest = created;

  while (Date.now() - startedAt < timeoutMs) {
    if (latest.status === 'succeeded') {
      return latest;
    }

    if (latest.status === 'failed') {
      throw new DashboardImportError(latest.error_message ?? 'Dashboard import job failed.', latest);
    }

    await wait(pollMs);
    latest = await getDashboardImportJob(created.id, fetcher);
  }

  throw new DashboardImportError('Dashboard import job timed out.', latest);
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    globalThis.setTimeout(resolve, ms);
  });
}
