/** Root web layout that applies fonts and global dashboard styles. */
import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import localFont from 'next/font/local';

import { CommandPaletteMount } from '../components/command-palette/CommandPaletteMount';
import './globals.css';

const sfProRounded = localFont({
  variable: '--font-sf-pro-rounded',
  display: 'swap',
  src: [
    { path: '../font/SF-Pro-Rounded-Thin.otf', weight: '100', style: 'normal' },
    { path: '../font/SF-Pro-Rounded-Ultralight.otf', weight: '200', style: 'normal' },
    { path: '../font/SF-Pro-Rounded-Light.otf', weight: '300', style: 'normal' },
    { path: '../font/SF-Pro-Rounded-Regular.otf', weight: '400', style: 'normal' },
    { path: '../font/SF-Pro-Rounded-Medium.otf', weight: '500', style: 'normal' },
    { path: '../font/SF-Pro-Rounded-Semibold.otf', weight: '600', style: 'normal' },
    { path: '../font/SF-Pro-Rounded-Bold.otf', weight: '700', style: 'normal' },
    { path: '../font/SF-Pro-Rounded-Heavy.otf', weight: '800', style: 'normal' },
    { path: '../font/SF-Pro-Rounded-Black.otf', weight: '900', style: 'normal' },
  ],
});

export const metadata: Metadata = {
  icons: [{ rel: 'icon', url: '/icon.svg', type: 'image/svg+xml' }],
};

const interactionScript = `
(() => {
  const themeStorageKey = 'electronic-mail-theme';
  const dashboardViewStorageKey = 'electronic-mail-dashboard-view';
  const defaultViewSettings = {
    brief: true,
    calendar: true,
    now: true,
    today: true,
    worthKnowing: true,
  };
  const viewDatasetKeys = {
    brief: 'showBrief',
    calendar: 'showCalendar',
    now: 'showNow',
    today: 'showToday',
    worthKnowing: 'showWorthKnowing',
  };

  function isThemeMode(value) {
    return value === 'light' || value === 'dark';
  }

  function getSystemTheme() {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function getStoredTheme() {
    const storedTheme = window.localStorage.getItem(themeStorageKey);
    return isThemeMode(storedTheme) ? storedTheme : null;
  }

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
  }

  function normalizeViewSettings(value) {
    if (value === null || typeof value !== 'object') {
      return { ...defaultViewSettings };
    }

    return Object.keys(defaultViewSettings).reduce((settings, key) => {
      settings[key] = typeof value[key] === 'boolean' ? value[key] : defaultViewSettings[key];
      return settings;
    }, {});
  }

  function readViewSettings() {
    try {
      const storedSettings = window.localStorage.getItem(dashboardViewStorageKey);
      return normalizeViewSettings(storedSettings === null ? null : JSON.parse(storedSettings));
    } catch (_error) {
      return { ...defaultViewSettings };
    }
  }

  function writeViewSettings(settings) {
    try {
      window.localStorage.setItem(dashboardViewStorageKey, JSON.stringify(settings));
    } catch (_error) {}
  }

  function syncViewControls(settings) {
    document.querySelectorAll('[data-dashboard-view-control]').forEach((control) => {
      const key = control.getAttribute('data-dashboard-view-control');
      if (key !== null && key in settings) {
        control.checked = settings[key];
      }
    });
  }

  function applyViewSettings(settings) {
    Object.keys(defaultViewSettings).forEach((key) => {
      document.documentElement.dataset[viewDatasetKeys[key]] = settings[key] ? 'true' : 'false';
    });
    syncViewControls(settings);
  }

  function readViewControls() {
    const settings = readViewSettings();
    document.querySelectorAll('[data-dashboard-view-control]').forEach((control) => {
      const key = control.getAttribute('data-dashboard-view-control');
      if (key !== null && key in settings) {
        settings[key] = Boolean(control.checked);
      }
    });
    return settings;
  }

  try {
    applyTheme(getStoredTheme() ?? getSystemTheme());
    applyViewSettings(readViewSettings());
  } catch (_error) {}

  document.addEventListener('click', (event) => {
    if (!(event.target instanceof Element)) {
      return;
    }

    const themeButton = event.target.closest('[data-theme-toggle]');
    if (themeButton !== null) {
      event.preventDefault();
      const currentTheme = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
      const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
      try {
        window.localStorage.setItem(themeStorageKey, nextTheme);
      } catch (_error) {}
      applyTheme(nextTheme);
      return;
    }

    const resetButton = event.target.closest('[data-dashboard-view-reset]');
    if (resetButton !== null) {
      event.preventDefault();
      const settings = { ...defaultViewSettings };
      writeViewSettings(settings);
      applyViewSettings(settings);
      return;
    }

  });

  document.addEventListener('change', (event) => {
    if (!(event.target instanceof Element)) {
      return;
    }

    const viewControl = event.target.closest('[data-dashboard-view-control]');
    if (viewControl === null) {
      return;
    }

    const settings = readViewControls();
    writeViewSettings(settings);
    applyViewSettings(settings);
  });

  const syncAfterDomReady = () => {
    applyViewSettings(readViewSettings());
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', syncAfterDomReady, { once: true });
  } else {
    syncAfterDomReady();
  }
})();
`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${sfProRounded.variable} ${sfProRounded.className}`}>
        <script dangerouslySetInnerHTML={{ __html: interactionScript }} />
        {children}
        <CommandPaletteMount />
        <ThemeToggleButton />
      </body>
    </html>
  );
}

function ThemeToggleButton() {
  return (
    <button type="button" className="theme-toggle" data-theme-toggle aria-label="Switch theme" title="Switch theme">
      <span className="theme-toggle-moon" aria-hidden="true">
        <svg viewBox="0 0 24 24" className="theme-toggle-icon">
          <path d="M20.3 15.4A8.7 8.7 0 0 1 8.6 3.7a8.9 8.9 0 1 0 11.7 11.7Z" />
        </svg>
      </span>
      <span className="theme-toggle-sun" aria-hidden="true">
        <svg viewBox="0 0 24 24" className="theme-toggle-icon">
          <circle cx="12" cy="12" r="4.2" />
          <path d="M12 2.4v2.2M12 19.4v2.2M4.6 4.6l1.6 1.6M17.8 17.8l1.6 1.6M2.4 12h2.2M19.4 12h2.2M4.6 19.4l1.6-1.6M17.8 6.2l1.6-1.6" />
        </svg>
      </span>
    </button>
  );
}
