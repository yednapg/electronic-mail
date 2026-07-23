# Launch test matrix

Create one execution record for every row. Record its status (`Not run`, `Pass`, `Fail`, or `Blocked`), tester, date/time and time zone, build SHA, app version/build, backend release SHA, test account, environment/device, evidence link, and notes or issue link. A `Pass` requires every behavior named in the row. For `Fail`, identify the failed step and linked issue; for `Blocked`, identify the dependency and owner. Evidence must identify the row and build, redact sensitive data, and never include production database screenshots.

For parity-changing mail operations, `Pass` also means the resulting state is visible in both the app and Gmail web after synchronization and relaunch, not merely that a request returned `200`. Other rows use the acceptance criteria and evidence named in that row.

| Area | Required launch test | Evidence |
|---|---|---|
| Install | Clean Mac installs notarized DMG; Gatekeeper accepts it | Screenshot + `spctl` output |
| Auth | First sign-in, cancel, retry, revoked grant, expired session, sign-out/relaunch | Screen recording/log correlation ID |
| Full sync | Oldest mailbox mail, all Inbox/Sent/Draft/Spam/Trash/Archive items and order match Gmail after backfill | Counts and sampled IDs |
| Compose | To/Cc/Bcc, subject/body, attachment, failed-send retry, duplicate prevention | Gmail Sent + recipient receipt |
| Draft | Create/autosave, quit/relaunch, edit, attachment, discard, send | Gmail Draft/Sent comparison |
| Conversation | Reply, Reply All and Forward preserve recipients/threading and quoting | Gmail thread comparison |
| Actions | Read/unread, star/unstar, archive/unarchive, trash/restore/permanent delete confirmation, spam/not-spam | Both directions verified |
| Search | Sender, recipient, subject and body; clear/empty/error states | Result comparison |
| Attachments | Download/save/open and compose upload under App Sandbox | Clean-Mac filesystem test |
| Sync | Gmail-web changes arrive automatically; app changes reach Gmail; sleep/wake and reconnect recover | Timestamped video |
| Scale | Hundreds of rows, >50-message thread, large initial import, pagination, no stale/duplicate rows | Counts/performance capture |
| Failure | Offline, API restart, worker restart, DB transient failure, Gmail 429/5xx | User-visible recovery + ops health |
| Security | Open registration abuse limits, account isolation, data deletion, no secrets/content in logs | Test report/log sample |
| Accessibility | Keyboard-only compose/read/actions, VoiceOver labels/focus, contrast and text scaling | Accessibility checklist |
| Update | Install newer signed build over previous build without losing session/cache | Before/after evidence |

## Automated gates

- `npm ci`
- `npm run reproducibility:verify:test && npm run reproducibility:verify` (exact manifest/lock/runtime/container/Action policy)
- `npm run verify` (native macOS build-for-testing and tests)
- `npm run release:macos:preflight` (credential-free universal Release/package policy)
- `npm run verify:all` (native plus backend and supporting web checks)
- `npm run launch:verify:test` (deterministic fail-closed launch-verifier contract tests)
- Python dependency audit and `npm audit --omit=dev --audit-level=high`
- Container build and `/ready` against migrated Postgres
- Complete production launch verification using every required input:

  ```bash
  LAUNCH_VERIFY_MODE=production \
  BACKEND_URL=https://api.your-company.com \
  WEB_URL=https://www.your-company.com \
  OPS_BEARER_TOKEN='replace-with-ops-admin-session-token' \
  APPLE_DEVELOPMENT_TEAM=TEAMID1234 \
  EXPECTED_RELEASE_SHA=0123456789abcdef0123456789abcdef01234567 \
  EXPECTED_VERSION=1.0.0 \
  EXPECTED_BUILD_NUMBER=1 \
  APP_PATH=/absolute/path/archive/ElectronicMail.app \
  DMG_PATH=/absolute/path/artifacts/ElectronicMail-1.0.0-1.dmg \
  RELEASE_METADATA_PATH=/absolute/path/artifacts/RELEASE-METADATA-1.0.0-1.json \
  SHA256SUMS_PATH=/absolute/path/artifacts/SHA256SUMS-1.0.0-1.txt \
  LAUNCH_ACCEPTANCE_PATH=/absolute/path/evidence/launch-acceptance-1.0.0-1.json \
  EVIDENCE_ORIGIN=https://evidence.your-owned-domain.com \
  EVIDENCE_BEARER_TOKEN='read-only-evidence-token' \
  MAX_QUEUE_DEPTH_PER_QUEUE=1000 \
  MAX_TOTAL_QUEUE_DEPTH=2500 \
  MAX_OLDEST_QUEUED_AGE_SECONDS=900 \
  npm run launch:verify
  ```

Replace every sample value with the reviewed release values; in particular, `EXPECTED_RELEASE_SHA` is the full source commit recorded in the release metadata and deployed by the API and workers. Release metadata must also record the approved Xcode 16.4 version and 16F6 build; the launch gate rejects toolchain drift. Copy `docs/launch-acceptance.template.json` into the controlled evidence archive, bind its release object to those exact values, and set a row to `pass` or an approval to `approved` only after its named evidence exists. The DMG, metadata, and checksum manifest must be files in the same artifact directory. The queue limits shown are the defaults and may be lowered. They cannot be raised above 10,000 jobs per queue, 25,000 total queued jobs, or 3,600 seconds for the oldest queued job.

The production gate fails unless the acceptance manifest contains every row above with `status=pass`, an accountable tester email, a time-zoned timestamp after the exact DMG was created, a redacted account label, a macOS-version environment, and a unique non-root HTTPS evidence record on `EVIDENCE_ORIGIN`. Every record includes a unique lowercase SHA-256; the verifier downloads it with the read-only `EVIDENCE_BEARER_TOKEN`, rejects redirects/non-200 responses, and checks its exact bytes. It also requires accountable `product_owner`, `privacy_legal`, `google_oauth_security`, `support_on_call`, and `production_operations` approvals bound to the exact DMG SHA-256, source commit, version, build, and deployed origins. Acceptance must be completed within 14 days. It then requires `/health`, `/ready`, and authenticated ops health to report the exact expected release, readiness and ops to report `environment=production`, every fresh worker to match the API release, all required queues to be served, dead/stale job counts to be zero, and the queue limits to pass. Finally, it validates the checksummed release metadata, deeply verifies both `APP_PATH` and the separate `ElectronicMail.app` actually mounted from `DMG_PATH`, and downloads the public DMG to require a byte-for-byte SHA-256 match.

- Release script Developer ID/team/application-identifier, Hardened Runtime, notarization, stapling, Gatekeeper, universal architecture, privacy manifest, backend injection, icon and checksum checks

No public link is published until every automated gate is green and every manual row has named evidence.
