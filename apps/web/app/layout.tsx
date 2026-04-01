import type { ReactNode } from 'react';
import localFont from 'next/font/local';

import './globals.css';

const sfProRounded = localFont({
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
  variable: '--font-sf-pro-rounded',
  display: 'swap',
});

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className={`${sfProRounded.className} ${sfProRounded.variable}`}>{children}</body>
    </html>
  );
}
