/** Root web layout that applies fonts and global dashboard styles. */
import type { ReactNode } from 'react';
import localFont from 'next/font/local';

import { ThemeToggle } from '../components/ThemeToggle';
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

const themeScript = `
(() => {
  try {
    const storedTheme = window.localStorage.getItem('decision-pipeline-theme');
    const theme =
      storedTheme === 'light' || storedTheme === 'dark'
        ? storedTheme
        : window.matchMedia('(prefers-color-scheme: dark)').matches
          ? 'dark'
          : 'light';
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
  } catch (_error) {}
})();
`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${sfProRounded.variable} ${sfProRounded.className}`}>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
        {children}
        <ThemeToggle />
      </body>
    </html>
  );
}
