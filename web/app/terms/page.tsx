import type { Metadata } from 'next';

import { LegalPage } from '../../components/legal/LegalPage';
import { loadLegalConfig } from '../../lib/legal-config';

export const metadata: Metadata = { title: 'Terms — Electronic Mail' };
export const dynamic = 'force-dynamic';

export default function TermsPage() {
  const config = loadLegalConfig();
  return (
    <LegalPage title="Terms of Service">
      <section>
        <h2>Agreement and account</h2>
        <p>
          These terms govern your use of Electronic Mail, operated by {config.entityName}. You must be legally able to
          enter this agreement, use an account you control, and keep your device and credentials secure. Google&apos;s
          terms continue to govern Gmail and your Google account.
        </p>
      </section>
      <section>
        <h2>Permitted use</h2>
        <p>
          You may use the service for lawful personal or business email. Do not access another person&apos;s account,
          send spam or malicious content, bypass limits, probe the service, infringe rights, or interfere with other
          users or infrastructure.
        </p>
      </section>
      <section>
        <h2>Your content and instructions</h2>
        <p>
          You retain rights in your messages and attachments. You authorize us to process them only as needed to run the
          service and carry out actions you request. You are responsible for recipients, content, legality, and keeping
          appropriate copies of important communications.
        </p>
      </section>
      <section>
        <h2>Availability and changes</h2>
        <p>
          Email delivery and synchronization depend on Google, networks, devices, and our infrastructure. We work to
          keep the service reliable but cannot promise uninterrupted or error-free operation. We may change or suspend
          features for security, legal, or operational reasons and will provide reasonable notice when practical.
        </p>
      </section>
      <section>
        <h2>Suspension and termination</h2>
        <p>
          You may stop using the service and disconnect Google at any time. We may restrict or terminate access for
          abuse, material breach, security risk, legal requirement, or discontinuation. Data handling after termination
          follows the Privacy Policy.
        </p>
      </section>
      <section>
        <h2>Disclaimers and liability</h2>
        <p>
          To the extent permitted by law, the service is provided “as is” without implied warranties. Neither party
          excludes liability that cannot legally be excluded. Mandatory consumer protections remain unaffected.
        </p>
      </section>
      <section>
        <h2>Governing law and contact</h2>
        <p>
          These terms are governed by the laws applicable in {config.jurisdiction}, subject to mandatory consumer law.
          Contact <a href={`mailto:${config.supportEmail}`}>{config.supportEmail}</a> with questions or notices.
        </p>
      </section>
    </LegalPage>
  );
}
