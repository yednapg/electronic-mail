import { notFound } from 'next/navigation';

import { isWebProductUIEnabled } from '../../../../lib/web-product-boundary';
import { GmailInboxClient } from '../../GmailInboxClient';

type GmailThreadPageProps = {
  params: Promise<{
    threadId: string;
  }>;
};

export default async function GmailThreadPage({ params }: GmailThreadPageProps) {
  if (!isWebProductUIEnabled()) {
    notFound();
  }

  const { threadId } = await params;
  return <GmailInboxClient initialThreadId={threadId} />;
}
