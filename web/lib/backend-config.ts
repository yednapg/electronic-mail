const DEFAULT_BACKEND_URL = 'http://localhost:3001';

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

export function resolveBackendURL(
  environment: Record<string, string | undefined> = process.env,
): string {
  const configuredValue = environment.ELECTRONIC_MAIL_BACKEND_URL;
  const rawValue = configuredValue === undefined || configuredValue === ''
    ? DEFAULT_BACKEND_URL
    : configuredValue;
  if (rawValue !== rawValue.trim() || [...rawValue].some((character) => character.charCodeAt(0) < 33)) {
    throw new Error('Backend origin contains whitespace or control characters');
  }

  let url: URL;
  try {
    url = new URL(rawValue);
  } catch {
    throw new Error('Backend origin is not a valid URL');
  }
  if (
    url.username
    || url.password
    || url.pathname !== '/'
    || url.search
    || url.hash
  ) {
    throw new Error('Backend URL must be an origin without credentials, path, query, or fragment');
  }

  const production = environment.NODE_ENV === 'production' || environment.APP_ENV === 'production';
  if (production) {
    const hostname = url.hostname.toLowerCase().replace(/\.$/, '');
    if (url.protocol !== 'https:') {
      throw new Error('Production backend origin must use HTTPS');
    }
    if (!isPublicHostname(hostname)) {
      throw new Error('Production backend origin must use a public non-reserved hostname');
    }
  } else if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error('Backend origin must use HTTP or HTTPS');
  }

  return url.origin;
}

function isPublicHostname(hostname: string): boolean {
  if (
    !hostname.includes('.')
    || hostname.includes(':')
    || /^[\d.]+$/.test(hostname)
    || RESERVED_HOSTS.has(hostname)
    || RESERVED_HOST_SUFFIXES.some((suffix) => hostname.endsWith(suffix))
  ) {
    return false;
  }
  return hostname.split('.').every((label) => (
    label.length > 0
    && label.length <= 63
    && /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(label)
  ));
}
