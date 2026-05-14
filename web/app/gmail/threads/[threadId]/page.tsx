import { GmailInboxClient } from '../../GmailInboxClient';

type GmailThreadPageProps = {
  params: Promise<{
    threadId: string;
  }>;
};

export default async function GmailThreadPage({ params }: GmailThreadPageProps) {
  const { threadId } = await params;
  return <GmailInboxClient initialThreadId={threadId} />;
}
