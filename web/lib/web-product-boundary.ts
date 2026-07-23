const PRODUCT_UI_PREFIXES = ['/gmail', '/dashboard'] as const;

type WebRuntimeEnvironment = Record<string, string | undefined>;

export function isProductionWebRuntime(
  environment: WebRuntimeEnvironment = process.env,
): boolean {
  return (
    environment.NODE_ENV?.trim().toLowerCase() === 'production'
    || environment.APP_ENV?.trim().toLowerCase() === 'production'
  );
}

export function isWebProductUIEnabled(
  environment: WebRuntimeEnvironment = process.env,
): boolean {
  return !isProductionWebRuntime(environment);
}

export function isWebProductPath(pathname: string): boolean {
  const normalizedPath = pathname.split(/[?#]/, 1)[0] || '/';
  return (
    PRODUCT_UI_PREFIXES.some((prefix) => (
      normalizedPath === prefix || normalizedPath.startsWith(`${prefix}/`)
    ))
    || normalizedPath === '/api'
    || normalizedPath.startsWith('/api/')
  );
}

export function shouldBlockProductionWebPath(
  pathname: string,
  environment: WebRuntimeEnvironment = process.env,
): boolean {
  return isProductionWebRuntime(environment) && isWebProductPath(pathname);
}
