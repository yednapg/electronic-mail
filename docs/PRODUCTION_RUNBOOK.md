# Production runbook

Electronic Mail is a native client backed by a FastAPI service, Postgres, three durable queue-worker services serving four queues, one Gmail recovery poller, and Google Pub/Sub. For the no-AI launch, explicitly disable the AI flags and leave `OPENAI_API_KEY` empty.

## Runtime topology

Deploy these processes from the same immutable commit and dependency lock:

| Process | Command/config | Minimum replicas |
|---|---|---:|
| Public OAuth/legal/support web | `railway.web.json` | 2 in production |
| API | `backend/railway.json` | 2 in production |
| Critical/default worker | `backend/railway.worker-fast.json` | 1 |
| Reader worker | `backend/railway.worker-reader.json` | 1 |
| Slow/backfill worker | `backend/railway.worker-slow.json` | 1 |
| Gmail recovery poller | `backend/railway.worker-poller.json` | 1 |
| Postgres | Managed Postgres 17 with PITR | Managed HA |

For every Railway API/worker service, set **Root Directory** to `/backend` and point **Config as Code** at its corresponding `railway*.json`. Every backend config builds `backend/Dockerfile`, so API and worker services run the same non-root Python 3.12 image installed from `requirements.lock`; only their start commands differ. The public web service instead uses repository root, `railway.web.json`, and `Dockerfile.web`.

The web deployment is not an email client. Its production boundary returns `404` for `/gmail`, `/dashboard`, nested product routes, and the legacy web `/api` BFF. It serves only the native-app landing/download page, browser OAuth completion, and public privacy, terms, and support pages. Point `DECISION_PIPELINE_BACKEND_URL` at the public production HTTPS API origin; the server rejects local, reserved, credential-bearing, and path-bearing values. Configure `MACOS_DOWNLOAD_ENABLED=true` with an owned public HTTPS `MACOS_DOWNLOAD_URL`, a monitored non-reserved `LEGAL_SUPPORT_EMAIL`, and an owned public HTTPS `STATUS_PAGE_URL`; placeholder, `.test`, `.invalid`, localhost, non-HTTPS, and no-reply values keep the affected launch surface unavailable. Set `MACOS_DOWNLOAD_ENABLED=false` to pause downloads during an incident.

The API and public web receive public traffic. Workers, the Gmail poller, and Postgres stay private. The API must sit behind a trusted TLS proxy that overwrites `CF-Connecting-IP`/`X-Forwarded-For`; application throttling is per process and is a backup, not a global edge control.

The public web build emits HSTS, CSP/anti-framing, no-referrer, MIME-sniffing, opener/resource isolation, and restrictive permissions headers. `/post-login` is additionally `no-store`. The production launch verifier treats any missing header as a failed gate; preserve these headers at the CDN/edge.

Mobile OAuth handoff state is persisted in Postgres, so API replicas do not require sticky sessions. Use one replica in staging and at least two across failure domains for public production.

## Secrets and configuration

Start from `deploy/production.env.example`. Store values in the host's encrypted secret store. Generate `APP_SESSION_SECRET` and `APP_ENCRYPTION_KEY` independently, with at least 32 random characters each. Never put secrets in Docker build arguments, Xcode settings, logs, screenshots, or support tickets.

The deployment guard refuses to start unless:

- Postgres, HTTPS origins, Google OAuth, authenticated Pub/Sub, and secure cookie settings are present;
- the API and public web share the configured cookie domain and `SESSION_COOKIE_SAMESITE=lax`, which supports the top-level OAuth return while withholding the session from cross-site subrequests;
- `GMAIL_SYNC_SCOPE=full` and `RATE_LIMIT_ENABLED=true`; `AI_GROUPING_ENABLED=false`, `OPENAI_REQUIRED=false`, and `OPENAI_DEBUG_LOGS=false`; and `OPENAI_API_KEY` is empty;
- staging uses `REGISTRATION_MODE=allowlist`; production explicitly chooses `allowlist` or `open`;
- allowlist mode has at least one `ALLOWED_EMAILS` value.

Run the guard without printing secret values:

```bash
PYTHONPATH=backend .venv/bin/python -m app.deploy_check
```

## Reproducible inputs

Run `npm run reproducibility:verify:test && npm run reproducibility:verify` before accepting any dependency or workflow change. The gate requires every direct Node dependency to be an exact version matching `package-lock.json`, every installed npm package to carry an integrity digest, every Python runtime dependency to be exactly pinned in `backend/requirements.lock`, exact Node/Python runtime versions, digest-pinned Dockerfile frontends and base images, digest-pinned CI service images, and commit-pinned third-party GitHub Actions. Production installs use `npm ci` and install `requirements.lock` with `--no-deps`, so an omitted transitive package cannot be resolved at an unreviewed version. Do not use `npm install`, `latest`, version ranges, or `requirements.txt` in a release image.

Digest pins intentionally prevent automatic base-image updates. Review vulnerability advisories, refresh the relevant tag/digest and lockfiles deliberately, run both container jobs, and record the change in the release review. A reproducible old image is not necessarily a secure image, so the npm and Python advisory audits remain separate launch gates.

## First deployment

1. Provision production Postgres with encryption, automated daily backups, point-in-time recovery, and a tested restore target.
2. Configure the production environment on every service.
3. Apply migrations once from the API pre-deploy hook (`alembic upgrade head`). Do not run schema migrations concurrently from every worker.
4. Start the three queue-worker services and Gmail poller, then the API.
5. Check `/health`, `/ready`, and authenticated `/v1/ops/health`.
6. Complete the full manual matrix in `docs/LAUNCH_TEST_MATRIX.md` with a non-owner Gmail test account and record the five accountable approvals in a copy of `docs/launch-acceptance.template.json`.
7. Run the fail-closed production verifier with that completed acceptance manifest, the reviewed release identity, and complete artifact set, using the exact inputs below.

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

Replace all sample values with the approved production values. Use the full release commit SHA, not a shortened display SHA, and copy the approved DMG's lowercase SHA-256 into the acceptance manifest before collecting evidence. Record the SHA-256 of every immutable evidence object and use a read-only evidence token; the verifier downloads and hashes each object without following redirects. Keep the DMG, release metadata, and `SHA256SUMS` manifest together in the release artifact directory; retain the completed acceptance manifest in the controlled evidence archive. The queue thresholds shown are the defaults and may be lowered for a quiet launch; hard caps prevent raising them above 10,000 per queue, 25,000 total, or 3,600 seconds oldest age. `LAUNCH_VERIFY_MODE=production` rejects all skip and insecure overrides. For an explicitly non-production smoke test only, `LAUNCH_VERIFY_MODE=non-production` preserves `SKIP_OPS_HEALTH=1`, `SKIP_ARTIFACT_VERIFY=1`, `SKIP_PUBLIC_PAGE_VERIFY=1`, `SKIP_ACCEPTANCE_VERIFY=1`, and `ALLOW_INSECURE_LAUNCH_VERIFY=1`; such a run prints that it is not production approval.

`/health` proves only that the process is alive. The production verifier additionally requires `/health` and `/ready` to report the exact expected release. `/ready` must report production configuration, Postgres connectivity, and exact Alembic head. `/v1/ops/health` must report the same production release/environment, matching fresh worker releases, required queue coverage, no dead or stale-running jobs, and a bounded backlog. The artifact gate authenticates the checksum manifest and clean, notarized release metadata before deeply verifying both the supplied app and the app mounted from the DMG. Finally, it downloads the DMG currently linked from the public page and requires the served bytes to have the same SHA-256 digest as the approved `DMG_PATH`; a stale upload cannot pass.

## Global edge rate limits

Configure these limits at Cloudflare, an API gateway, or the hosting edge, keyed by authenticated account when possible and trusted client IP otherwise:

| Route class | Suggested ceiling |
|---|---:|
| OAuth start/callback | 20/minute/IP |
| Mobile handoff polling | 180/minute/IP |
| Mobile code exchange | 20/minute/IP |
| Manual mailbox sync | 12/minute/account |
| Compose/reply/forward/draft writes | 90/minute/account |
| Attachment download | 120/minute/account |

Return `429` with `Retry-After`. Alert on sustained throttling, OAuth failures, dead jobs, stale workers, 5xx rate, readiness failure, and oldest queued job age. Do not log URL query strings, authorization/cookie headers, OAuth codes, recipients, subjects, bodies, or attachment content.

## Backup and restore

Managed PITR is the primary recovery mechanism. Also create authenticated encrypted, access-controlled logical backups from a private runner. Provision a dedicated 32-byte backup key as exactly 64 lowercase hexadecimal characters (for example, from a managed KMS-backed secret), keep it separate from application encryption keys, and install the repository's exact locked Python environment before running the job:

```bash
BACKUP_ENCRYPTION_KEY="$BACKUP_KEY_FROM_SECRET_STORE" \
DATABASE_URL='postgresql://…' \
BACKUP_DIR=/secure/backups \
LEGAL_BACKUP_RETENTION_DAYS=14 \
./scripts/backup-postgres.sh
```

The script verifies the installed `cryptography` version against `backend/requirements.lock`, streams the custom-format dump directly through AES-256-GCM, and atomically publishes only a mode-0600 `.dump.enc` artifact plus its SHA-256 transport-integrity sidecar. It never writes the plaintext dump or encryption key to a temporary file. The key and raw database URL are removed from the inherited environment before any external helper runs; the key is exposed only to the pinned crypto helper during validation and encryption, while the raw URL is exposed only to the sanitizer. Copy the encrypted artifacts off-host to versioned encrypted object storage and retain the corresponding secret version for the full retention window.

The backup job prevents concurrent runs and removes local artifacts whose exact age exceeds `LEGAL_BACKUP_RETENTION_DAYS` (14 days by default) only after a new encrypted backup completes; a failed run never prunes the last known-good recovery point. This is a recovery-safety policy, not a hard deletion guarantee: monitor the scheduled job and alert on any overdue encrypted artifact, because a prolonged runner outage delays local expiry. The published privacy notice discloses that operational exception. Managed object storage must have an independently configured lifecycle rule and alert so its expiry does not depend on this backup command. If a runner is forcibly terminated and leaves `.electronic-mail-backup.lock`, first prove no backup process is running before removing that lock manually. Restore only into an isolated database first:

```bash
CONFIRM_RESTORE=RESTORE \
BACKUP_ENCRYPTION_KEY="$BACKUP_KEY_FROM_SECRET_STORE" \
RESTORE_DATABASE_URL='postgresql://…/electronic_mail_restore' \
EXPECTED_RESTORE_DATABASE=electronic_mail_restore \
BACKUP_FILE=/secure/backups/electronic-mail-TIMESTAMP.dump.enc \
./scripts/restore-postgres.sh
```

Restore verifies the SHA-256 sidecar and authenticates the complete AES-GCM archive before streaming plaintext directly into a single-transaction `pg_restore`; it never stores a decrypted dump or key on disk. The key and raw restore URL are removed from the inherited environment before any external helper runs. The key is exposed only to the pinned crypto helper during validation and decryption, while the raw URL is exposed only to the sanitizer; neither reaches `pg_restore`. `ALLOW_UNVERIFIED_RESTORE=1` can bypass a missing sidecar only for a reviewed isolated recovery; it never bypasses authenticated decryption and is rejected for `RESTORE_TARGET_CLASS=production`. `EXPECTED_RESTORE_DATABASE` must exactly match the URL database name, preventing a copied command from silently targeting another database. The default target class is `isolated` and rejects the conventional primary database names. A reviewed production restore additionally requires `RESTORE_TARGET_CLASS=production` and `CONFIRM_PRODUCTION_RESTORE=RESTORE_PRODUCTION_DATABASE`; do not use that path for routine drills. After restore, run migrations, `/ready`, data-count checks, and a Gmail sync using a test account before switching traffic. Record a quarterly restore drill with restore time and data-loss window.

## Deploy and rollback

Before each deploy, take/confirm a restorable backup and verify the migration is backward compatible with the previous API. Deploy workers, then API. Keep the previous immutable image and macOS download available.

Rollback application code only when the prior code supports the current schema. Alembic downgrades are not an automatic incident response; restore to a new database if a migration corrupted data. Rotate potentially exposed OAuth, session, encryption, database, Pub/Sub, and notarization credentials immediately.
