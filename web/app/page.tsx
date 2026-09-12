/** Public landing page for the native macOS product. */
import React from 'react';

import { AppMark } from '../components/AppMark';
import {
  downloadAvailability,
  loadDownloadConfig,
  type DownloadConfig,
} from '../lib/download-config';

export const dynamic = 'force-dynamic';

export default function HomePage() {
  return <HomePageContent downloadConfig={loadDownloadConfig()} />;
}

export function HomePageContent({ downloadConfig }: { downloadConfig: DownloadConfig }) {
  const downloadState = downloadAvailability(downloadConfig);

  return (
    <main className="login-page">
      <div className="login-shell">
        <AppMark className="login-mark" />

        <div className="login-copy">
          <h1 className="login-title">
            <span className="login-title-line">Electronic Mail.</span>
            <span className="login-title-line">Built for your Mac.</span>
          </h1>
          <p className="login-native-description">
            A focused, native Gmail client for macOS. Open the installed app to connect Google and manage your mail.
          </p>
        </div>

        {downloadState === 'available' ? (
          <a className="login-primary-link" href={downloadConfig.url}>
            Download for macOS
          </a>
        ) : (
          <p className="login-native-description">Downloads are temporarily unavailable.</p>
        )}
      </div>
    </main>
  );
}
