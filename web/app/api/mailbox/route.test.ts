import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const mailboxRouteSource = readFileSync(new URL('./route.ts', import.meta.url), 'utf8');

test('/api/mailbox forwards the browser cookie to the backend mailbox request', () => {
  assert.match(mailboxRouteSource, /const cookie = getRequestCookieHeader\(request\)/);
  assert.match(mailboxRouteSource, /getMailbox\(\{ label, limit, cursor \}, \{ cookie \}\)/);
});
