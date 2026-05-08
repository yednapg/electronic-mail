/** Demo landing screen that sends users into the dashboard flow. */
import Link from 'next/link';
import { redirect } from 'next/navigation';

import { AppMark } from '../components/AppMark';
import { getBackendURL, getDashboard, isDemoMode } from '../lib/api';
import { DEMO_SIGN_IN_ROUTE } from '../lib/demo-flow';
import type { DashboardResponse } from '../lib/types';

export default async function HomePage() {
  const demoMode = isDemoMode();
  let signInHref = demoMode ? DEMO_SIGN_IN_ROUTE : `${getBackendURL()}/auth/google`;
  let dashboard: DashboardResponse | null = null;

  if (!demoMode) {
    try {
      dashboard = await getDashboard();
    } catch {
      signInHref = `${getBackendURL()}/auth/google`;
    }
  }

  if (dashboard?.auth.connected) {
    redirect('/dashboard');
  }

  signInHref = dashboard?.auth.connect_url ?? signInHref;

  return (
    <main className="login-page">
      <div className="login-shell">
        <AppMark className="login-mark" />

        <div className="login-copy">
          <h1 className="login-title">
            <span className="login-title-line">Work first.</span>
            <span className="login-title-line">Emails underneath.</span>
          </h1>
        </div>

        <Link className="login-google-button" href={signInHref}>
          <span className="login-google-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" role="img" aria-hidden="true">
              <path
                fill="#4285F4"
                d="M23.49 12.27c0-.79-.07-1.55-.2-2.27H12v4.3h6.44a5.5 5.5 0 0 1-2.39 3.61v3h3.87c2.27-2.09 3.57-5.17 3.57-8.64Z"
              />
              <path
                fill="#34A853"
                d="M12 24c3.24 0 5.96-1.07 7.95-2.9l-3.87-3c-1.07.72-2.43 1.14-4.08 1.14-3.14 0-5.8-2.12-6.76-4.97H1.24v3.09A12 12 0 0 0 12 24Z"
              />
              <path
                fill="#FBBC05"
                d="M5.24 14.27A7.2 7.2 0 0 1 4.86 12c0-.79.14-1.55.38-2.27V6.64H1.24A12 12 0 0 0 0 12c0 1.93.46 3.76 1.24 5.36l4-3.09Z"
              />
              <path
                fill="#EA4335"
                d="M12 4.77c1.76 0 3.33.61 4.57 1.8l3.43-3.43C17.95 1.19 15.23 0 12 0A12 12 0 0 0 1.24 6.64l4 3.09c.96-2.85 3.62-4.96 6.76-4.96Z"
              />
            </svg>
          </span>
          <span>Continue with Google</span>
        </Link>
      </div>
    </main>
  );
}
