import type { DashboardImportJobResponse } from '@electronic-mail/types';

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
  timeoutMs?: number | null;
  pollMs?: number;
  onUpdate?: (job: DashboardImportJobResponse) => void;
}): Promise<DashboardImportJobResponse> {
  const created = await createDashboardImportJob(fetcher);
  const startedAt = Date.now();
  let latest = created;
  onUpdate?.(latest);

  while (timeoutMs === null || timeoutMs === undefined || Date.now() - startedAt < timeoutMs) {
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
  const longRunning = isLongRunningStage(job);

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
    case 'source_summary':
      return formatCountedStatus(
        longRunning ? 'Still summarizing email evidence' : 'Summarizing email evidence',
        job.source_records || job.imported_count,
      );
    case 'memory_hydration':
      return formatCountedStatus(
        longRunning ? 'Still grouping related emails into work' : 'Grouping related emails into work',
        job.source_records || job.imported_count,
      );
    case 'ai_refresh':
      return formatCountedStatus(
        longRunning ? 'Still finding current state and next move' : 'Finding current state and next move',
        job.changed_entities,
      );
    case 'feed_build':
      return formatCountedStatus(
        longRunning ? 'Still preparing refreshed dashboard items' : 'Preparing refreshed dashboard items',
        job.refreshed_entities || job.changed_entities,
      );
    case 'briefing':
      return longRunning ? 'Still writing your morning brief...' : 'Writing your morning brief...';
    case 'completed':
      return 'Dashboard is ready.';
    case 'failed':
      return 'Preparation hit a problem. Please try again.';
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

function formatCountedStatus(message: string, count: number): string {
  return count > 0 ? `${message} for ${count} items...` : `${message}...`;
}

function isLongRunningStage(job: DashboardImportJobResponse): boolean {
  if (job.status !== 'running' || typeof job.stage_started_at !== 'string') {
    return false;
  }

  const startedAt = new Date(job.stage_started_at).getTime();
  if (!Number.isFinite(startedAt)) {
    return false;
  }

  return Date.now() - startedAt > 30000;
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    globalThis.setTimeout(resolve, ms);
  });
}
