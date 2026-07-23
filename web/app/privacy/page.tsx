import type { Metadata } from 'next';

import { LegalPage } from '../../components/legal/LegalPage';
import { loadLegalConfig } from '../../lib/legal-config';

export const metadata: Metadata = { title: 'Privacy — Electronic Mail' };
export const dynamic = 'force-dynamic';

export default function PrivacyPage() {
  const config = loadLegalConfig();
  return (
    <LegalPage title="Privacy Policy">
      <section>
        <h2>What this service does</h2>
        <p>
          Electronic Mail connects to a Google account at your direction and provides a native interface for reading,
          searching, organizing, drafting, and sending Gmail messages. {config.entityName} operates the service.
        </p>
      </section>
      <section>
        <h2>Information we process</h2>
        <ul>
          <li>Google account identifiers, email address, display name, OAuth authorization, and app sessions.</li>
          <li>Gmail message headers, participants, labels, dates, bodies, threads, drafts, and attachments.</li>
          <li>Mailbox actions, synchronization state, delivery errors, and security/operational events.</li>
          <li>Local app cache and preferences stored on your Mac.</li>
        </ul>
      </section>
      <section>
        <h2>How we use information</h2>
        <p>
          We use this information only to provide and secure email functionality, synchronize requested changes with
          Gmail, prevent abuse, troubleshoot failures, and comply with law. This no-AI release does not send Gmail data
          to OpenAI or use Gmail content to train AI models. We do not sell Gmail data or use it for advertising.
        </p>
      </section>
      <section>
        <h2>Google API data</h2>
        <p>
          Our use and transfer of information received from Google APIs adheres to the Google API Services User Data
          Policy, including its Limited Use requirements. You can revoke access in your Google Account permissions at
          any time.
        </p>
      </section>
      <section>
        <h2>Sharing and storage</h2>
        <p>
          Information is shared only with Google to perform Gmail operations, infrastructure providers that host and
          protect the service, and authorities when legally required. Our hosting providers are {config.hostingProviders},
          and production data is processed in {config.dataRegions}. Production access is restricted. Google OAuth
          credentials are encrypted at rest, transport uses HTTPS, and the native app stores its production session in
          the macOS Keychain.
        </p>
      </section>
      <section>
        <h2>Retention and deletion</h2>
        <p>
          Live mailbox-derived data is retained while your account is connected so the app can synchronize. You can
          disconnect Google and request deletion using the app controls or by contacting support. Deleted data in
          protected backups is scheduled to expire after {config.backupRetentionDays} days. An outage in the backup
          expiry system may delay that deletion; access remains restricted while the overdue expiry is remediated.
          Legal or security obligations may also require limited records to be retained longer.
        </p>
      </section>
      <section>
        <h2>Your choices and contact</h2>
        <p>
          You may disconnect Google, revoke sessions, delete Gmail-derived data, or request access/correction/deletion.
          Contact <a href={`mailto:${config.supportEmail}`}>{config.supportEmail}</a>. Depending on where you live, you
          may have additional privacy rights and a right to complain to a data-protection authority.
        </p>
      </section>
      <section>
        <h2>Changes</h2>
        <p>Material changes will be posted here with a new effective date before they take effect.</p>
      </section>
    </LegalPage>
  );
}
