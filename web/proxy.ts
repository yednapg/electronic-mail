import type { NextRequest } from 'next/server';
import { NextResponse } from 'next/server';

import { shouldBlockProductionWebPath } from './lib/web-product-boundary';

export function proxy(request: NextRequest) {
  if (!shouldBlockProductionWebPath(request.nextUrl.pathname)) {
    return NextResponse.next();
  }

  return new NextResponse('Not Found', {
    status: 404,
    headers: {
      'Cache-Control': 'no-store',
      'Content-Type': 'text/plain; charset=utf-8',
      'X-Content-Type-Options': 'nosniff',
    },
  });
}

export const config = {
  matcher: ['/gmail/:path*', '/dashboard/:path*', '/api/:path*'],
};
