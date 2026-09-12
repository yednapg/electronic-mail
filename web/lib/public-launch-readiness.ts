import { resolveBackendURL } from './backend-config';
import { downloadAvailability, loadDownloadConfig } from './download-config';

export type PublicLaunchReadiness = {
  serving: boolean;
  ready: boolean;
  checks: {
    backend: boolean;
    download: boolean;
  };
};

/**
 * Resolve only non-secret public-launch configuration and fail closed when any
 * required surface would be broken or unapproved. The health response exposes
 * boolean categories rather than configured values.
 */
export function evaluatePublicLaunchReadiness(
  environment: Record<string, string | undefined> = process.env,
): PublicLaunchReadiness {
  let backendReady = true;
  try {
    resolveBackendURL(environment);
  } catch {
    backendReady = false;
  }

  const downloadState = downloadAvailability(loadDownloadConfig(environment));
  const checks = {
    backend: backendReady,
    download: downloadState === 'available',
  };

  return {
    serving: checks.backend && downloadState !== 'misconfigured',
    ready: Object.values(checks).every(Boolean),
    checks,
  };
}
