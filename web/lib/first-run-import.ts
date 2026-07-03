import type { FirstRunImportJobResponse } from './types';

const FIRST_RUN_POLL_MS = 1200;

export function isFirstRunImportReady(job: FirstRunImportJobResponse): boolean {
  return Boolean(job.inbox_ready_at && job.first_groups_ready_at && job.hot_window_ready_at);
}

export function formatFirstRunImportStatus(job: FirstRunImportJobResponse): string {
  if (isFirstRunImportReady(job)) {
    return 'Inbox is ready. Opening the app...';
  }
  if (job.first_groups_ready_at) {
    return 'Preparing your 30-day inbox...';
  }
  if (job.inbox_ready_at) {
    return 'Writing useful titles...';
  }

  switch (job.stage) {
    case 'queued':
      return 'Starting inbox setup...';
    case 'gmail_recent_sync':
    case 'gmail_listing':
      return 'Finding recent emails...';
    case 'importing_gmail':
    case 'gmail_fetching':
      return `Importing emails${job.total_count ? ` (${job.fetched_count}/${job.total_count})` : '...'}`;
    case 'gmail_persisted':
      return `Saving emails${job.fetched_count ? ` (${job.fetched_count})` : '...'}`;
    case 'inbox_projection':
      return 'Preparing your 30-day inbox...';
    case 'dashboard_fast_feed':
      return 'Preparing your priority inbox...';
    case 'mail_group_enrich':
    case 'ai_grouping':
    case 'ai_summarizing':
      return 'Grouping Gmail into real-life work...';
    case 'dashboard_filtering':
      return 'Finishing inbox priorities...';
    case 'ready':
      return 'Inbox is ready...';
    default:
      return 'Preparing your inbox...';
  }
}

export async function startFirstRunImportJob(signal?: AbortSignal): Promise<FirstRunImportJobResponse> {
  const response = await fetch('/api/first-run/import-jobs', {
    method: 'POST',
    cache: 'no-store',
    signal,
    headers: {
      Accept: 'application/json',
    },
  });

  if (!response.ok) {
    throw new Error('Inbox setup could not be started.');
  }

  return response.json();
}

export async function getFirstRunImportJob(
  jobId: string,
  signal?: AbortSignal,
): Promise<FirstRunImportJobResponse> {
  const response = await fetch(`/api/first-run/import-jobs/${encodeURIComponent(jobId)}`, {
    cache: 'no-store',
    signal,
    headers: {
      Accept: 'application/json',
    },
  });

  if (!response.ok) {
    throw new Error('Inbox setup status could not be loaded.');
  }

  return response.json();
}

export async function waitForFirstRunImportReady({
  onStatus,
  signal,
}: {
  onStatus?: (message: string) => void;
  signal?: AbortSignal;
} = {}): Promise<FirstRunImportJobResponse> {
  let job = await startFirstRunImportJob(signal);
  onStatus?.(formatFirstRunImportStatus(job));

  while (!isFirstRunImportReady(job)) {
    if (job.status === 'failed') {
      throw new Error(job.error_message || 'Inbox setup failed. Please try again.');
    }

    await wait(FIRST_RUN_POLL_MS, signal);
    job = await getFirstRunImportJob(job.id, signal);
    onStatus?.(formatFirstRunImportStatus(job));
  }

  return job;
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('The operation was aborted.', 'AbortError'));
      return;
    }

    const timeout = window.setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);

    function onAbort() {
      window.clearTimeout(timeout);
      reject(new DOMException('The operation was aborted.', 'AbortError'));
    }

    signal?.addEventListener('abort', onAbort, { once: true });
  });
}
