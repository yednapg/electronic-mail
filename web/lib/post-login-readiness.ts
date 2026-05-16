import type { PostLoginReadinessResponse } from './types';
import { startFirstRunImportJob } from './first-run-import';

const READINESS_POLL_MS = 1200;

export async function getPostLoginReadiness(options: { signal?: AbortSignal } = {}): Promise<PostLoginReadinessResponse> {
  if (options.signal?.aborted) {
    throw new DOMException('Post-login readiness polling aborted', 'AbortError');
  }
  const response = await fetch('/api/post-login/readiness', {
    cache: 'no-store',
    credentials: 'include',
    signal: options.signal,
    headers: {
      Accept: 'application/json',
    },
  });
  if (!response.ok) {
    throw new Error('Post-login readiness failed');
  }
  return response.json() as Promise<PostLoginReadinessResponse>;
}

export async function waitForPostLoginReady(options: {
  signal?: AbortSignal;
  onUpdate?: (readiness: PostLoginReadinessResponse) => void;
} = {}): Promise<PostLoginReadinessResponse> {
  const initialReadiness = await getPostLoginReadiness({ signal: options.signal });
  options.onUpdate?.(initialReadiness);
  if (initialReadiness.ready_to_enter) {
    return initialReadiness;
  }
  await startFirstRunImportJob(options.signal);
  while (true) {
    if (options.signal?.aborted) {
      throw new DOMException('Post-login readiness polling aborted', 'AbortError');
    }
    const readiness = await getPostLoginReadiness({ signal: options.signal });
    options.onUpdate?.(readiness);
    if (readiness.ready_to_enter) {
      return readiness;
    }
    if (readiness.stage === 'failed') {
      throw new Error(readiness.error_message || 'Setup failed. Please refresh.');
    }
    await wait(READINESS_POLL_MS, options.signal);
  }
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(resolve, ms);
    signal?.addEventListener(
      'abort',
      () => {
        window.clearTimeout(timeout);
        reject(new DOMException('Post-login readiness polling aborted', 'AbortError'));
      },
      { once: true },
    );
  });
}
