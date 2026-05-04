import { AppMark } from '../../components/AppMark';
import { RedirectToDashboard } from './redirect-to-dashboard';
import { RotatingStatus } from './rotating-status';

export default function PostLoginPage() {
  return (
    <main className="post-login-page" aria-label="Preparing your dashboard">
      <RedirectToDashboard />
      <section className="post-login-shell">
        <AppMark className="post-login-mark" />

        <div className="post-login-copy">
          <h1 className="post-login-title">
            <span className="post-login-title-line">Preparing your dashboard</span>
          </h1>
          <RotatingStatus />
          <a className="post-login-fallback-link" href="/dashboard">
            See dashboard
          </a>
        </div>
      </section>
    </main>
  );
}
