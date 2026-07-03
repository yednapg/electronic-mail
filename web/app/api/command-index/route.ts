import { NextResponse } from 'next/server';

import { getAppSession } from '../../../lib/api';
import { getRequestCookieHeader } from '../../../lib/api-auth';
import { buildCommandIndexFromAppSession, getStaticCommands } from '../../../lib/command-index';

export async function GET(request: Request) {
  const scope = new URL(request.url).searchParams.get('scope');
  if (scope === 'static') {
    return NextResponse.json({ generatedAt: new Date().toISOString(), commands: getStaticCommands() });
  }

  try {
    const cookie = getRequestCookieHeader(request);
    const session = await getAppSession({ cookie });
    return NextResponse.json(buildCommandIndexFromAppSession(session, new Date()));
  } catch (_error) {
    return NextResponse.json(buildCommandIndexFromAppSession(null));
  }
}
