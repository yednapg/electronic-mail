# Production runbook

Electronic Mail is a native client backed by a FastAPI service, a Postgres 17-compatible pgvector service, four durable queue-worker services, one Gmail recovery poller, and Google Pub/Sub. AI Inbox is an isolated, kill-switchable projection; the normal Gmail Inbox does not depend on it.

## Runtime topology

Deploy these processes from the same immutable commit and dependency lock:

| Process | Command/config | Minimum replicas |
|---|---|---:|
| Public OAuth/legal/support web | `railway.web.json` | 2 in production |
| API | `backend/railway.json` | 2 in production |
| Critical/default worker | `backend/railway.worker-fast.json` | 1 |
| Reader worker | `backend/railway.worker-reader.json` | 1 |
| Slow/backfill worker | `backend/railway.worker-slow.json` | 1 |
| AI organization worker | `backend/railway.worker-ai.json` | 1 |
| Gmail recovery poller | `backend/railway.worker-poller.json` | 1 |
| Postgres + pgvector | Pinned Postgres 17-compatible pgvector service with PITR | Managed HA |

For every Railway API/worker service, set **Root Directory** to `/backend` and point **Config as Code** at its corresponding `railway*.json`. Every backend config builds `backend/Dockerfile`, so API and worker services run the same non-root Python 3.12 image installed from `requirements.lock`; only their start commands differ. The shared image health check is **process liveness only**: it gives the schema startup gate its full wait window, verifies the API over its local `/health` endpoint, and recognizes the reviewed worker/poller PID 1 entrypoints without incorrectly probing an HTTP port they do not serve. It is not a worker promotion gate. Worker operational readiness comes exclusively from fresh database heartbeats, exact release parity, exact fast/reader/slow/AI/poller queue-role coverage, and the authenticated `/v1/ops/health` gate. The public web service instead uses repository root, `railway.web.json`, and `Dockerfile.web`.

GitHub-triggered Railway deployments expose `RAILWAY_GIT_COMMIT_SHA`; the backend uses that immutable value as `release_sha` in the API and every heartbeat. Do not manually copy `RELEASE_SHA` into Railway. If both variables exist and disagree, startup fails. Non-Railway production platforms must set a full immutable `RELEASE_SHA` themselves.

The web deployment is not an email client. Its production boundary returns `404` for `/gmail`, `/dashboard`, nested product routes, and the legacy web `/api` BFF. It serves only the native-app landing/download page, browser OAuth completion, and public privacy, terms, and support pages. Point `DECISION_PIPELINE_BACKEND_URL` at the public production HTTPS API origin; the server rejects local, reserved, credential-bearing, and path-bearing values. Configure `MACOS_DOWNLOAD_ENABLED=true` with an owned public HTTPS `MACOS_DOWNLOAD_URL`, a monitored non-reserved `LEGAL_SUPPORT_EMAIL`, and an owned public HTTPS `STATUS_PAGE_URL`; placeholder, `.test`, `.invalid`, localhost, non-HTTPS, and no-reply values keep the affected launch surface unavailable. Set `MACOS_DOWNLOAD_ENABLED=false` to pause downloads during an incident.

The API and public web receive public traffic. Workers, the Gmail poller, and Postgres stay private. The API must sit behind a trusted TLS proxy that overwrites `CF-Connecting-IP`/`X-Forwarded-For`; application throttling is per process and is a backup, not a global edge control.

The public web build emits HSTS, CSP/anti-framing, no-referrer, MIME-sniffing, opener/resource isolation, and restrictive permissions headers. `/post-login` is additionally `no-store`. Its `/healthz` endpoint reports separate production-backend, public-DMG-download, and approved-legal/support checks. Backend or legal misconfiguration returns `503`; a deliberate download pause returns HTTP `200` with `status=degraded` so Privacy, Terms, and Support stay online. Railway and the container health check use that endpoint instead of a content page, while the production launch verifier requires `status=ready` and all three checks true. Preserve these responses and headers at the CDN/edge.

Mobile OAuth handoff state is persisted in Postgres, so API replicas do not require sticky sessions. Use one replica in staging and at least two across failure domains for public production.

## Secrets and configuration

Start from `deploy/production.env.example`. Store values in the host's encrypted secret store. Generate `APP_SESSION_SECRET` and `APP_ENCRYPTION_KEY` independently, with at least 32 random characters each. Never put secrets in Docker build arguments, Xcode settings, logs, screenshots, or support tickets.

The deployment guard refuses to start unless:

- Postgres, HTTPS origins, Google OAuth, authenticated Pub/Sub, and secure cookie settings are present;
- the API and public web share the configured cookie domain and `SESSION_COOKIE_SAMESITE=lax`, which supports the top-level OAuth return while withholding the session from cross-site subrequests;
- `GMAIL_SYNC_SCOPE=full` and `RATE_LIMIT_ENABLED=true`; the retired `AI_GROUPING_ENABLED`, `OPENAI_REQUIRED`, and `OPENAI_DEBUG_LOGS` flags stay explicitly false;
- `AI_INBOX_ENABLED` is explicit. When true, an OpenAI key is required; when false, the key must be empty so disabled deployments cannot make provider calls;
- staging uses `REGISTRATION_MODE=allowlist`; production explicitly chooses `allowlist` or `open`;
- allowlist mode has at least one `ALLOWED_EMAILS` value.

Run the guard without printing secret values:

```bash
PYTHONPATH=backend .venv/bin/python -m app.deploy_check
```

Production startup requires `DATABASE_URL` to use hostname-authenticated
PostgreSQL TLS: `sslmode=verify-full`, `gssencmode=disable`, and exactly one
`sslrootcert=system` or readable absolute CA-bundle path. Install or mount the
reviewed CA bundle in every API, worker, poller, migration, backup, and restore
runtime. Encryption-only `sslmode=require` is rejected because it does not
authenticate the database peer.

## Reproducible inputs

Run `npm run reproducibility:verify:test && npm run reproducibility:verify` before accepting any dependency or workflow change. The gate requires every direct Node dependency to be an exact version matching `package-lock.json`, every installed npm package to carry an integrity digest, every Python runtime dependency to be exactly pinned in `backend/requirements.lock`, exact Node/Python runtime versions, digest-pinned Dockerfile frontends and base images, digest-pinned CI service images, and commit-pinned third-party GitHub Actions. `.python-version` is the canonical backend/Linux pin, Python 3.12.13. Hosted macOS quality and release automation instead uses Python 3.12.10, the exact patch available on `macos-15-intel`; the verifier derives the required pin from each setup-python job's static `runs-on` context and fails on missing, dynamic, or mismatched context. CI also performs a hash-only binary download of every runtime-lock and audit-lock pin for CPython 3.12 on macOS Intel. `scripts/refresh-python-lock-hashes.py` applies the same target proof before atomically replacing a lock; `cryptography==48.0.1` is retained because 49.0.0 does not publish a compatible macOS Intel wheel. Production installs use `npm ci` and install `requirements.lock` with `--no-deps`, so an omitted transitive package cannot be resolved at an unreviewed version. Do not use `npm install`, `latest`, version ranges, or `requirements.txt` in a release image.

Digest pins intentionally prevent automatic base-image updates. Review vulnerability advisories, refresh the relevant tag/digest and lockfiles deliberately, run both container jobs, and record the change in the release review. A reproducible old image is not necessarily a secure image, so the npm and Python advisory audits remain separate launch gates.

## First deployment

1. Provision a pinned Postgres 17-compatible service with pgvector, encryption, automated daily backups, point-in-time recovery, and a tested restore target. Railway's default Postgres image does not bundle extensions; use its pgvector extension template or an equivalent managed service.
2. Configure the production environment on every service.
3. Restore the latest production backup into an isolated target, apply `alembic upgrade head`, run `python -m app.schema_check`, compare row counts and constraints, and prove that the backup can be restored before changing `DATABASE_URL`. Keep the old database read-only during the rollback window.
4. Apply migrations once from the API pre-deploy hook (`alembic upgrade head`). Do not run schema migrations concurrently from every worker. Readiness now verifies both the vector extension and every AI Inbox table/column.
5. Start the four queue-worker services and Gmail poller, then the API. Keep `AI_INBOX_ENABLED=false` until the shadow projection is ready.
6. Configure GitHub repository secrets `PRODUCTION_BACKEND_URL` and `PRODUCTION_OPS_BEARER_TOKEN`; the bearer must be an app session for an `OPS_ADMIN_EMAILS` account and must be rotated like any other privileged credential.
7. Require `.github/workflows/verify-production-backend.yml` before release promotion. Railway's production `deployment_status=success` event starts the verifier, which waits for three consecutive exact-release snapshots across `/health`, `/ready`, and authenticated `/v1/ops/health`. It proves Postgres/schema readiness, zero dead/stale jobs, bounded queues, fresh heartbeats, all worker releases matching the API, and one or more instances of each exact fast, reader, slow, AI, and poller role. The ops endpoint remains authenticated; no public worker-readiness endpoint is added.
8. Process the allowlisted user's latest 30 days into a shadow generation. Promote only after the AI Inbox precision, evidence, cost, latency, and zero-false-merge gates pass. Model, prompt, embedding, or threshold changes require a new shadow generation; never mutate an active projection in place.
9. Complete the full manual matrix in `docs/LAUNCH_TEST_MATRIX.md` with a non-owner Gmail test account and record the five accountable approvals in a copy of `docs/launch-acceptance.template.json`.
10. Run the fail-closed production verifier with that completed acceptance manifest, the reviewed release identity, and complete artifact set, using the exact inputs below.

Railway config-as-code currently has a pre-deploy command and HTTP healthcheck path, but no multi-service post-deploy command. GitHub-triggered services also deploy independently. Therefore a worker `healthcheckPath` would be false (workers expose no HTTP server), and no individual service can prove whole-release convergence. The external exact-release workflow is the truthful promotion boundary. A green Railway service status is not production approval; keep download/traffic promotion blocked until this workflow and the complete launch verifier pass. A failed post-deploy gate cannot atomically undo already started Railway containers, so rollback remains an explicit operator action; use an isolated staging environment first when zero exposure before verification is required.

```bash
LAUNCH_VERIFY_MODE=production \
BACKEND_URL=https://api.your-company.com \
WEB_URL=https://www.your-company.com \
OPS_BEARER_TOKEN='replace-with-ops-admin-session-token' \
APPLE_DEVELOPMENT_TEAM=TEAMID1234 \
EXPECTED_RELEASE_SHA=0123456789abcdef0123456789abcdef01234567 \
EXPECTED_VERSION=1.0.0 \
EXPECTED_BUILD_NUMBER=1 \
APP_PATH="/absolute/path/archive/Electronic Mail.app" \
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

The critical worker must continuously serve `google_token_revoke` jobs. A reconnect is intentionally blocked while durable revocation cleanup remains active for that Google subject, because Google revocation invalidates the subject's project-wide grant rather than only one displaced refresh token. Do not cancel or delete these jobs merely to unblock sign-in; first independently confirm provider revocation, then use a reviewed database recovery procedure and retain an audit record.

`/health` proves only that the process is alive. The production verifier additionally requires `/health` and `/ready` to report the exact expected release. `/ready` must report production configuration, Postgres connectivity, and exact Alembic head. `/v1/ops/health` must report the same production release/environment, matching fresh worker releases, required queue coverage, no dead or stale-running jobs, and a bounded backlog. The artifact gate authenticates the checksum manifest and clean, notarized release metadata before deeply verifying both the supplied app and the app mounted from the DMG. Finally, it downloads the DMG currently linked from the public page and requires the served bytes to have the same SHA-256 digest as the approved `DMG_PATH`; a stale upload cannot pass.

## Global edge rate limits

Configure these limits at Cloudflare, an API gateway, or the hosting edge, keyed by authenticated account when possible and trusted client IP otherwise:

| Route class | Suggested ceiling |
|---|---:|
| OAuth start/callback | 20/minute/IP |
| Mobile handoff polling | 180/minute/IP |
| Mobile code exchange | 20/minute/IP |
| Gmail Pub/Sub verification | 60/minute/trusted source IP |
| Manual mailbox sync | 12/minute/account |
| Compose/reply/forward/draft writes | 90/minute/account |
| Attachment download | 120/minute/account |

Return `429` with `Retry-After`. Alert on sustained throttling, OAuth failures, dead jobs, stale workers, 5xx rate, readiness failure, and oldest queued job age. Do not log URL query strings, authorization/cookie headers, OAuth codes, recipients, subjects, bodies, or attachment content.

## Backup and restore

Managed PITR is the primary recovery mechanism. Also create authenticated encrypted, access-controlled logical backups from a private runner. Provision a dedicated 32-byte backup key as exactly 64 lowercase hexadecimal characters (for example, from a managed KMS-backed secret), keep it separate from application encryption keys, and install the repository's exact locked Python environment before running the job:

```bash
BACKUP_ENCRYPTION_KEY="$BACKUP_KEY_FROM_SECRET_STORE" \
DATABASE_URL='postgresql://…@db.example.com:5432/electronic_mail?sslmode=verify-full&sslrootcert=system&gssencmode=disable' \
EXPECTED_BACKUP_DATABASE=electronic_mail \
EXPECTED_BACKUP_HOST=db.example.com \
EXPECTED_BACKUP_PORT=5432 \
BACKUP_DIR=/secure/backups \
LEGAL_BACKUP_RETENTION_DAYS=14 \
./scripts/backup-postgres.sh
```

The script requires the reviewed PostgreSQL 17.10-or-newer 17.x `pg_dump` client, verifies the installed `cryptography` version against `backend/requirements.lock`, binds the reviewed host, port, and database to the parsed URL, and requires `sslmode=verify-full`, `gssencmode=disable`, and an explicit `sslrootcert=system` or existing absolute CA-bundle path. It clears inherited libpq and OpenSSL routing/trust variables before validation. It then streams the custom-format dump directly through AES-256-GCM and atomically publishes only a mode-0600 `.dump.enc` artifact plus its SHA-256 transport-integrity sidecar. It never writes the plaintext dump or encryption key to a temporary file. The key and raw database URL are removed from the inherited environment before any external helper runs; the key is exposed only to the pinned crypto helper during validation and encryption, while the raw URL is exposed only to the sanitizer. Copy the encrypted artifacts off-host to versioned encrypted object storage and retain the corresponding secret version for the full retention window.

The backup job prevents concurrent runs and removes local artifacts whose exact age exceeds `LEGAL_BACKUP_RETENTION_DAYS` (14 days by default) only after a new encrypted backup completes; a failed run never prunes the last known-good recovery point. This is a recovery-safety policy, not a hard deletion guarantee: monitor the scheduled job and alert on any overdue encrypted artifact, because a prolonged runner outage delays local expiry. The published privacy notice discloses that operational exception. Managed object storage must have an independently configured lifecycle rule and alert so its expiry does not depend on this backup command. If a runner is forcibly terminated and leaves `.electronic-mail-backup.lock`, first prove no backup process is running before removing that lock manually. Restore only into a newly created, empty, isolated database first:

```bash
CONFIRM_RESTORE=RESTORE \
CONFIRM_DIRECT_RESTORE_ENDPOINTS=DIRECT_SINGLE_CLUSTER \
RESTORE_FINALIZE_TIMEOUT_SECONDS=3600 \
BACKUP_ENCRYPTION_KEY="$BACKUP_KEY_FROM_SECRET_STORE" \
RESTORE_DATABASE_URL='postgresql://…@db.example.com:5432/electronic_mail_restore?sslmode=verify-full&sslrootcert=system&gssencmode=disable' \
RESTORE_MAINTENANCE_DATABASE_URL='postgresql://…@db.example.com:5432/postgres?sslmode=verify-full&sslrootcert=system&gssencmode=disable' \
EXPECTED_RESTORE_DATABASE=electronic_mail_restore \
BACKUP_FILE=/secure/backups/electronic-mail-TIMESTAMP.dump.enc \
./scripts/restore-postgres.sh
```

Restore requires reviewed PostgreSQL 17.10-or-newer 17.x `psql` and `pg_restore` clients and the same server patch floor. Both URLs must be direct, non-pooler endpoints for one PostgreSQL cluster; transaction/session poolers and DNS endpoints that can move a connection between clusters are forbidden, and `CONFIRM_DIRECT_RESTORE_ENDPOINTS=DIRECT_SINGLE_CLUSTER` records that operator check. It verifies the SHA-256 sidecar and authenticates the complete AES-GCM archive before streaming the decrypted custom archive through a connectionless `pg_restore --file=-`; neither the decrypted archive nor generated SQL is written to a regular file. A single long-lived target `psql` session acquires the per-target restore advisory lock, identifies itself with a cryptographically random application name, and owns the explicit restore transaction. A separately authenticated, long-lived maintenance `psql` session must target the `postgres` database on the identical canonical host and port. The maintenance role must be able to inspect `pg_stat_activity`, alter the target database, terminate every other target backend, and re-enable connections; the target role must own and restore into the target database. The maintenance session proves the nonce-bound target identity, commits `ALLOW_CONNECTIONS false`, terminates every backend except that target session, and proves it is the sole remaining target backend before any restore mutation begins. Concurrent restore attempts fail before they can alter connection state.

Inside that same target transaction, a fail-closed catalog preflight proves the database has only the pristine PostgreSQL 17.10 `public` schema, including its expected owner, ACL, comment, and absence of security labels or user objects. A non-pristine target, failed probe, archive/decryption/generation error, target-session failure, or signal before `COMMIT` terminates the target session without sending `COMMIT`, so PostgreSQL rolls back the entire restore; cleanup then re-enables target connections. Independent cryptographic pre-commit and post-commit sentinels prove statement completion without exposing their nonce to restored SQL. `RESTORE_FINALIZE_TIMEOUT_SECONDS` controls both potentially long final-index and WAL-flush waits, defaults to 3600 seconds, and accepts 60–86400. If the post-commit acknowledgment is missing, the outcome is treated as indeterminate: connections stay disabled and an operator must inspect the target before deciding whether to re-enable or retry. If connection re-enablement fails, the command also exits critically and traffic must remain off until an operator uses the reviewed maintenance connection to run `ALTER DATABASE ... ALLOW_CONNECTIONS true`.

The keys and raw target and maintenance URLs are removed from the inherited environment before external helpers run. The key is exposed only to the pinned crypto helper; each sanitized URL and its mode-0600 password file reaches only its corresponding `psql`; `pg_restore` is connectionless. `ALLOW_UNVERIFIED_RESTORE=1` can bypass a missing sidecar only for a reviewed isolated recovery; it never bypasses authenticated decryption and is rejected for `RESTORE_TARGET_CLASS=production`. `EXPECTED_RESTORE_DATABASE` must exactly match the target URL database name. The default target class is `isolated` and rejects conventional primary database names. A reviewed production restore additionally requires `RESTORE_TARGET_CLASS=production`, `CONFIRM_PRODUCTION_RESTORE=RESTORE_PRODUCTION_DATABASE`, `EXPECTED_RESTORE_HOST=<canonical database host>`, and `EXPECTED_RESTORE_PORT=<database port>`; those expected fields and both URL endpoints must match exactly. Both production URLs must set `sslmode=verify-full`, `gssencmode=disable`, and an explicit `sslrootcert=system` or existing absolute CA-bundle path. The script clears inherited libpq and OpenSSL trust-routing variables before validation.

Keep application traffic off until restore, migrations, `/ready`, data-count checks, and a Gmail sync using a test account have all passed. If the restore coordinator is forcibly killed while connections are disabled, first prove no restore is still running, then manually re-enable the target through the reviewed maintenance connection before retrying or directing traffic. Do not use the production path for routine drills. Record a quarterly restore drill with restore time and data-loss window.

## Deploy and rollback

Before each deploy, take/confirm a restorable backup and verify the migration is backward compatible with the previous API. Deploy workers, then API. Keep the previous immutable image and macOS download available.

Changes to the per-user provider/deletion advisory-lock protocol require a coordinated cutover, not a mixed-version rolling deployment. Stop admitting API traffic (including disconnect, Gmail-data deletion, and account deletion), gracefully drain and stop every old API, worker, and poller process, deploy the same new immutable release to every backend process, and verify matching release SHAs in authenticated ops health before restoring traffic. Never allow an old deletion replica to run beside provider workers using a newer lock namespace; the old replica cannot drain work on a lock key it does not know.

Rollback application code only when the prior code supports the current schema. Alembic downgrades are not an automatic incident response; restore to a new database if a migration corrupted data. Rotate potentially exposed OAuth, session, encryption, database, Pub/Sub, and notarization credentials immediately.
