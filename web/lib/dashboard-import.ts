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
  onUpdate,
}: {
  fetcher?: Fetcher;
  timeoutMs: number;
  pollMs?: number;
  onUpdate?: (job: DashboardImportJobResponse) => void;
}): Promise<DashboardImportJobResponse> {
  const created = await createDashboardImportJob(fetcher);
  const startedAt = Date.now();
  let latest = created;
  onUpdate?.(latest);

  while (Date.now() - startedAt < timeoutMs) {
    if (latest.status === 'succeeded') {
      return latest;
    }

    if (latest.status === 'failed') {
      throw new DashboardImportError(latest.error_message ?? 'Dashboard import job failed.', latest);
    }

    await wait(pollMs);
    latest = await getDashboardImportJob(created.id, fetcher);
    onUpdate?.(latest);
  }

  throw new DashboardImportError('Dashboard import job timed out.', latest);
}

export function formatDashboardImportStatus(job: DashboardImportJobResponse): string {
  const progress = formatImportProgress(job);

  switch (job.stage) {
    case 'queued':
    case 'starting':
      return 'Connecting to Gmail...';
    case 'gmail_listing':
      return 'Reading your mailbox index...';
    case 'gmail_fetching':
      return progress ? `Fetching Gmail threads ${progress}...` : 'Fetching Gmail threads...';
    case 'gmail_persisted':
      return progress ? `Saving Gmail evidence ${progress}...` : 'Saving Gmail evidence...';
    case 'memory_hydration':
      return 'Grouping related emails into work...';
    case 'ai_refresh':
      return 'Finding current state and next move...';
    case 'feed_build':
      return 'Preparing your dashboard...';
    case 'briefing':
      return 'Writing your morning brief...';
    case 'completed':
      return 'Dashboard is ready.';
    case 'failed':
      return 'Preparation hit a problem. Opening the dashboard...';
    default:
      return 'Preparing your dashboard...';
  }
}

function formatImportProgress(job: DashboardImportJobResponse): string | null {
  if (job.total_count === null || job.total_count === undefined || job.total_count <= 0) {
    return job.imported_count > 0 ? `${job.imported_count}` : null;
  }

  return `${Math.min(job.imported_count, job.total_count)}/${job.total_count}`;
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    globalThis.setTimeout(resolve, ms);
  });
}
