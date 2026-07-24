import { NextResponse } from 'next/server';

import { evaluatePublicLaunchReadiness } from '../../lib/public-launch-readiness';

export const dynamic = 'force-dynamic';

export function createHealthResponse(environment: Record<string, string | undefined>) {
  const readiness = evaluatePublicLaunchReadiness(environment);
  const status = readiness.ready
    ? 'ready'
    : readiness.serving
      ? 'degraded'
      : 'not_ready';

  return NextResponse.json(
    {
      status,
      checks: readiness.checks,
    },
    {
      status: readiness.serving ? 200 : 503,
      headers: {
        'Cache-Control': 'no-store',
      },
    },
  );
}

export function GET() {
  return createHealthResponse(process.env);
}
