import type { Metadata } from 'next';

import { LegalPage } from '../../components/legal/LegalPage';
import { loadLegalConfig } from '../../lib/legal-config';

export const metadata: Metadata = { title: 'Support — Electronic Mail' };
export const dynamic = 'force-dynamic';

export default function SupportPage() {
  const config = loadLegalConfig();
  const subject = encodeURIComponent('Electronic Mail support request');
  const securitySubject = encodeURIComponent('Electronic Mail security report');
  return (
    <LegalPage title="Support">
      <section>
        <h2>Contact us</h2>
        <p>
          Email <a href={`mailto:${config.supportEmail}?subject=${subject}`}>{config.supportEmail}</a> for sign-in,
          synchronization, sending, attachments, privacy, or account-deletion help. Include the app version, macOS
          version, approximate time, and any visible correlation ID.
        </p>
      </section>
      <section>
        <h2>Protect your account</h2>
        <p>
          Support will never ask for your Google password, OAuth authorization code, session token, raw email body, or
          production database screenshots. Revoke Electronic Mail from Google Account permissions immediately if you
          suspect unauthorized access.
        </p>
      </section>
      <section>
        <h2>Report a security issue</h2>
        <p>
          Send suspected vulnerabilities privately to{' '}
          <a href={`mailto:${config.supportEmail}?subject=${securitySubject}`}>{config.supportEmail}</a>. Include safe
          reproduction steps, impact, app version, macOS version, and a correlation ID if available. Do not post a
          public issue or send credentials, tokens, private email content, attachments, or database exports.
        </p>
      </section>
      <section>
        <h2>Service status</h2>
        <p>
          Check the <a href={config.statusPageURL}>Electronic Mail service status page</a> for current incidents and
          planned maintenance before retrying a failed synchronization or send.
        </p>
      </section>
      <section>
        <h2>Common recovery steps</h2>
        <ol>
          <li>Confirm Gmail works in your browser and your Mac is online.</li>
          <li>Use the app&apos;s refresh action and allow the current sync to finish.</li>
          <li>Quit and reopen the app; sign in again if permission was revoked.</li>
          <li>For persistent missing mail or send failure, contact support before repeatedly retrying.</li>
        </ol>
      </section>
      <section>
        <h2>Privacy requests</h2>
        <p>
          Use the app&apos;s disconnect/delete controls or email support from the connected account. We may need to verify
          account ownership before completing a request.
        </p>
      </section>
    </LegalPage>
  );
}
