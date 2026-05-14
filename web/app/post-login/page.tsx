import { redirect } from 'next/navigation';

import { getGoogleAuthState } from '../../lib/api';
import { getServerCookieHeader } from '../../lib/server-cookies';
import { RotatingStatus } from './rotating-status';

export default async function PostLoginPage() {
  const cookie = await getServerCookieHeader();
  const auth = await getGoogleAuthState({ cookie });
  if (!auth.connected) {
    redirect('/');
  }

  return (
    <main className="post-login-page" aria-label="Preparing your dashboard">
      <section className="post-login-shell">
        <div className="post-login-copy">
          <RotatingStatus />
        </div>
      </section>
    </main>
  );
}
