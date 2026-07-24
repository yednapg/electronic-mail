export type DownloadConfig = {
  enabled: boolean;
  url: string;
};

const RESERVED_HOSTS = new Set([
  'example.com',
  'example.net',
  'example.org',
  'localhost',
  'localhost.localdomain',
]);

const RESERVED_HOST_SUFFIXES = [
  '.example',
  '.example.com',
  '.example.net',
  '.example.org',
  '.invalid',
  '.localhost',
  '.local',
  '.test',
];

export function loadDownloadConfig(
  environment: Record<string, string | undefined> = process.env,
): DownloadConfig {
  return {
    enabled: environment.MACOS_DOWNLOAD_ENABLED?.trim().toLowerCase() === 'true',
    url: environment.MACOS_DOWNLOAD_URL?.trim() || '',
  };
}

export function downloadConfigIssues(config: DownloadConfig): string[] {
  const issues: string[] = [];
  if (!config.enabled) issues.push('downloads disabled');
  if (!isPublicHTTPSURL(config.url)) issues.push('macOS download URL');
  return issues;
}

function isPublicHTTPSURL(value: string): boolean {
  try {
    if (value !== value.trim() || /[\u0000-\u0020\u007f]/.test(value)) return false;
    const url = new URL(value);
    const hostname = url.hostname;
    const pathSegments = url.pathname.split('/').slice(1);
    return (
      url.protocol === 'https:'
      && url.username === ''
      && url.password === ''
      && url.port === ''
      && url.search === ''
      && url.hash === ''
      && value === url.href
      && hostname === hostname.toLowerCase()
      && !hostname.endsWith('.')
      && hostname.includes('.')
      && !hostname.includes(':')
      && !/^[\d.]+$/.test(hostname)
      && !RESERVED_HOSTS.has(hostname)
      && !RESERVED_HOST_SUFFIXES.some((suffix) => hostname.endsWith(suffix))
      && hostname.split('.').every((label) => (
        label.length > 0
        && label.length <= 63
        && /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(label)
      ))
      && pathSegments.length > 0
      && pathSegments.every((segment) => segment !== '' && segment !== '.' && segment !== '..')
      && /^[A-Za-z0-9._~/-]+\.dmg$/.test(url.pathname)
    );
  } catch {
    return false;
  }
}
