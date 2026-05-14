import Link from 'next/link';
import React from 'react';
import type { ReactNode } from 'react';

type AppSection = 'dashboard' | 'gmail';

type SignedInAppChromeProps = {
  active: AppSection;
  children?: ReactNode;
};

type DashboardViewSettings = {
  brief: boolean;
  calendar: boolean;
  now: boolean;
  today: boolean;
  worthKnowing: boolean;
};

type DashboardViewSettingKey = keyof DashboardViewSettings;

const VIEW_SETTING_OPTIONS: Array<{ key: DashboardViewSettingKey; label: string }> = [
  { key: 'brief', label: 'Brief' },
  { key: 'calendar', label: 'Calendar' },
  { key: 'now', label: 'Now' },
  { key: 'today', label: 'Today' },
  { key: 'worthKnowing', label: 'Worth Knowing' },
];

export function SignedInAppChrome({ active, children }: SignedInAppChromeProps) {
  return (
    <div className="signed-in-app">
      <nav className="app-chrome-nav" aria-label="Primary">
        <Link
          href="/dashboard"
          prefetch
          className={`app-chrome-link ${active === 'dashboard' ? 'is-active' : ''}`}
          aria-current={active === 'dashboard' ? 'page' : undefined}
        >
          Dashboard
        </Link>
        <Link
          href="/gmail"
          prefetch
          className={`app-chrome-link ${active === 'gmail' ? 'is-active' : ''}`}
          aria-current={active === 'gmail' ? 'page' : undefined}
        >
          Inbox
        </Link>
      </nav>
      <AppSettingsPanel />
      {children}
    </div>
  );
}

function AppSettingsPanel() {
  return (
    <details className="view-settings">
      <summary className="view-settings-button" aria-label="App settings" title="App settings">
        <SettingsIcon />
      </summary>
      <div id="dashboard-view-settings" className="view-settings-popover">
        <div className="view-settings-panel" aria-label="App settings">
          <div className="view-settings-header">
            <p className="view-settings-title">View</p>
            <button type="button" className="view-settings-reset" data-dashboard-view-reset>
              Reset
            </button>
          </div>
          <Link href="/dashboard" prefetch className="view-settings-link">
            Dashboard
          </Link>
          <Link href="/gmail" prefetch className="view-settings-link">
            Inbox
          </Link>
          <div className="view-settings-options">
            {VIEW_SETTING_OPTIONS.map((option) => (
              <label key={option.key} className="view-setting-row">
                <span className="view-setting-label">{option.label}</span>
                <input
                  type="checkbox"
                  className="view-setting-input"
                  data-dashboard-view-control={option.key}
                  defaultChecked
                  style={{ caretColor: 'transparent' }}
                  suppressHydrationWarning
                />
                <span className="view-setting-switch" aria-hidden="true">
                  <span className="view-setting-switch-knob" />
                </span>
              </label>
            ))}
          </div>
        </div>
      </div>
    </details>
  );
}

function SettingsIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="view-settings-icon">
      <circle cx="12" cy="12" r="3.2" />
      <path d="M19.4 13.4a7.7 7.7 0 0 0 .1-1.4 7.7 7.7 0 0 0-.1-1.4l2-1.5-2-3.5-2.4 1a8.1 8.1 0 0 0-2.4-1.4L14.2 2h-4.4l-.4 3.2A8.1 8.1 0 0 0 7 6.6l-2.4-1-2 3.5 2 1.5a7.7 7.7 0 0 0-.1 1.4 7.7 7.7 0 0 0 .1 1.4l-2 1.5 2 3.5 2.4-1a8.1 8.1 0 0 0 2.4 1.4l.4 3.2h4.4l.4-3.2a8.1 8.1 0 0 0 2.4-1.4l2.4 1 2-3.5-2-1.5Z" />
    </svg>
  );
}
