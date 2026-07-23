/** Root web layout that applies fonts and global dashboard styles. */
import type { Metadata } from 'next';
import type { ReactNode } from 'react';

import { CommandPaletteMount } from '../components/command-palette/CommandPaletteMount';
import { LayoutInteractions } from '../components/LayoutInteractions';
import { isWebProductUIEnabled } from '../lib/web-product-boundary';
import './globals.css';

export const metadata: Metadata = {
  title: 'Electronic Mail',
  description: 'A focused native email client for Gmail.',
  icons: [{ rel: 'icon', url: '/icon.svg', type: 'image/svg+xml' }],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <LayoutInteractions />
        {children}
        {isWebProductUIEnabled() ? <CommandPaletteMount /> : null}
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
