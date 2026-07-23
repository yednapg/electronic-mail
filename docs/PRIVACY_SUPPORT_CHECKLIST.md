# Privacy, OAuth, and support gates

These are launch gates requiring the product owner/legal publisher; repository automation cannot approve them.

## Privacy and data handling

- Publish an owned-domain privacy policy that accurately states that the service processes Gmail message content, headers, participants, attachments, OAuth tokens, and app account/session data to provide email functionality. Set the reviewed `LEGAL_HOSTING_PROVIDERS` and `LEGAL_DATA_REGIONS` disclosures; placeholders keep the public pages visibly unapproved.
- State retention/deletion timelines, subprocessors/hosting regions, encryption controls, incident contact, user rights, and how account deletion/revocation works.
- Publish Terms and a support page on the same verified domain; link all three from the Google OAuth consent screen and download page.
- Execute required processor agreements, restrict production/admin/database access, enable audit logs, and document incident response and breach notification owners.
- Treat the checked-in Apple privacy manifest as the code-audited, conservative taxonomy baseline. Validate it against actual production retention, logs, IP handling, subprocessors, purposes/linking/tracking, and the published policy. If an App Store submission is planned, also reconcile the App Store Connect privacy answers. Legal/product—not engineering automation—must sign off.
- Test “Disconnect Google”, app-account data deletion, Google grant revocation, and backup-expiry behavior. Deletion must include tokens, sessions, cached mail, derived data, and eventual backup expiry.

## Google OAuth production

- Use a separate production Google Cloud project owned by the publisher organization.
- Configure production branding, owned/verified domains, exact HTTPS redirect URI, homepage, privacy, Terms, and support links.
- Request only the Gmail scopes actually used. Restricted Gmail scopes require Google OAuth verification and a security assessment unless an applicable Google exemption applies.
- Do not launch from OAuth “Testing”: it is capped at 100 test users and authorizations can expire after seven days.
- Grant Pub/Sub publish rights on the topic to Gmail's service account, `gmail-api-push@system.gserviceaccount.com`, and use authenticated push with the exact audience and push-authentication service-account email configured in production.
- Keep `REGISTRATION_MODE=allowlist` until OAuth verification, abuse controls, on-call support, and deletion workflows are approved; switching to `open` is an explicit production change.

## Support readiness

- Replace placeholders with a monitored owned-domain `LEGAL_SUPPORT_EMAIL` and a public owned-domain HTTPS `STATUS_PAGE_URL`; reserved/test domains and no-reply addresses are rejected. Define business hours, on-call escalation, and severity response targets.
- Prepare articles for sign-in loops, permission revocation, delayed sync, missing mail, send failure, attachments, account deletion, and uninstall.
- Give support only correlation IDs and account IDs; never request OAuth codes, tokens, passwords, raw email bodies, or production database screenshots.
- Define and test the emergency kill switch: return registration to `allowlist`, set `MACOS_DOWNLOAD_ENABLED=false`, revoke compromised credentials, and publish incident updates.
- Create a release/incident owner roster and a go/no-go meeting record for each public build.
