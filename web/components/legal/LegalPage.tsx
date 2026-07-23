import Link from 'next/link';
import type { ReactNode } from 'react';

import { AppMark } from '../AppMark';
import { legalConfigIssues, loadLegalConfig } from '../../lib/legal-config';

export function LegalPage({ title, children }: { title: string; children: ReactNode }) {
  const config = loadLegalConfig();
  const requiresReview = legalConfigIssues(config).length > 0;

  return (
    <main className="legal-page">
      <header className="legal-header">
        <Link href="/" className="legal-brand" aria-label="Electronic Mail home">
          <AppMark className="legal-mark" />
          <span>Electronic Mail</span>
        </Link>
        <nav className="legal-nav" aria-label="Legal and support">
          <Link href="/privacy">Privacy</Link>
          <Link href="/terms">Terms</Link>
          <Link href="/support">Support</Link>
        </nav>
      </header>
      <article className="legal-document">
        {requiresReview ? (
          <p className="legal-review-banner" role="status">
            Draft for owner and legal review. This page is not approved for public launch.
          </p>
        ) : null}
        <p className="legal-eyebrow">{config.entityName}</p>
        <h1>{title}</h1>
        <p className="legal-effective">Effective {config.effectiveDate}</p>
        {children}
      </article>
    </main>
  );
}
