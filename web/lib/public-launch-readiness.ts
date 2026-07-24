import { resolveBackendURL } from './backend-config';
import { downloadAvailability, loadDownloadConfig } from './download-config';
import { legalConfigIssues, loadLegalConfig } from './legal-config';

export type PublicLaunchReadiness = {
  serving: boolean;
  ready: boolean;
  checks: {
    backend: boolean;
    download: boolean;
    legal: boolean;
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
    legal: legalConfigIssues(loadLegalConfig(environment)).length === 0,
  };

  return {
    // Only an explicit operational pause may keep Privacy, Terms, and Support
    // online with a degraded health response. Unknown or broken download
    // configuration is unsafe to present as an intentional pause.
    serving: checks.backend && checks.legal && downloadState !== 'misconfigured',
    ready: Object.values(checks).every(Boolean),
    checks,
  };
}
