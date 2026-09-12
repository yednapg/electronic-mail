import { redirect } from 'next/navigation';
import React from 'react';

import { AppMark } from '../../components/AppMark';
import { getGoogleAuthState } from '../../lib/api';
import { getServerCookieHeader } from '../../lib/server-cookies';

export const dynamic = 'force-dynamic';
const AUTH_STATE_TIMEOUT_MS = 5_000;

export type OAuthCompletionState = 'connected' | 'disconnected' | 'unavailable';

export async function resolveOAuthCompletionState(
  loadState: () => Promise<{ connected: boolean }>,
): Promise<OAuthCompletionState> {
  try {
    return (await loadState()).connected ? 'connected' : 'disconnected';
  } catch {
    return 'unavailable';
  }
}

export default async function PostLoginPage() {
  const cookie = await getServerCookieHeader();
  const state = await resolveOAuthCompletionState(() => getGoogleAuthState({
    cookie,
    signal: AbortSignal.timeout(AUTH_STATE_TIMEOUT_MS),
  }));
  if (state === 'disconnected') {
    redirect('/');
  }
  if (state === 'unavailable') {
    return <OAuthCompletionUnavailableView />;
  }

  return <OAuthCompletionView />;
}

export function OAuthCompletionView() {
  return (
    <main className="post-login-page" aria-label="Google connection complete">
      <section className="post-login-shell">
        <AppMark className="post-login-mark" />
        <div className="post-login-copy">
          <h1 className="post-login-title">Google account connected.</h1>
          <p className="post-login-completion-copy">
            Return to Electronic Mail on your Mac. You can close this browser window.
          </p>
        </div>
      </section>
    </main>
  );
}

export function OAuthCompletionUnavailableView() {
  return (
    <main className="post-login-page" aria-label="Google connection status unavailable">
      <section className="post-login-shell">
        <AppMark className="post-login-mark" />
        <div className="post-login-copy">
          <h1 className="post-login-title">Connection status unavailable.</h1>
          <p className="post-login-completion-copy">
            We could not confirm the connection right now. Return to Electronic Mail on your Mac; the app can safely
            retry when the service is available. This page has not changed your Google account.
          </p>
        </div>
      </section>
    </main>
  );
}
