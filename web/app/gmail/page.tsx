import { GmailInboxClient } from './GmailInboxClient';

export { GmailView, formatGmailReceivedAt } from './GmailView';

export default function GmailPage() {
  return <GmailInboxClient />;
}
