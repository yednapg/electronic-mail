import { NextResponse } from 'next/server';

import { getDashboard, getGmailView, getGoogleAuthState } from '../../../lib/api';
import { getRequestCookieHeader } from '../../../lib/api-auth';
import { buildCommandIndex, getStaticCommands } from '../../../lib/command-index';

export async function GET(request: Request) {
  const scope = new URL(request.url).searchParams.get('scope');
  if (scope === 'static') {
    return NextResponse.json({ generatedAt: new Date().toISOString(), commands: getStaticCommands() });
  }

  try {
    const cookie = getRequestCookieHeader(request);
    const auth = await getGoogleAuthState({ cookie });
    if (!auth.connected) {
      return NextResponse.json(buildCommandIndex(null));
    }
    const [dashboard, gmail] = await Promise.all([
      getDashboard({ cookie }).catch(() => null),
      getGmailView({ cookie }).catch(() => null),
    ]);
    return NextResponse.json(buildCommandIndex(dashboard, new Date(), gmail));
  } catch (_error) {
    return NextResponse.json(buildCommandIndex(null));
  }
}
