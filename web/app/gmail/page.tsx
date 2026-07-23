import { notFound } from 'next/navigation';

import { isWebProductUIEnabled } from '../../lib/web-product-boundary';
import { GmailInboxClient } from './GmailInboxClient';

export { GmailView, formatGmailReceivedAt } from './GmailView';

export default function GmailPage() {
  if (!isWebProductUIEnabled()) {
    notFound();
  }

  return <GmailInboxClient />;
}
