# Launch readiness ledger

This is the fail-closed status ledger for the no-AI Electronic Mail macOS launch. It maps the repository's launch requirements to the command or external record that proves each one. A configured gate is not a passed gate. Update this file only from the named evidence; never infer completion from implementation intent.

Status vocabulary:

- **Passed (local)**: the named deterministic command passed in the current worktree on the date recorded below. It must pass again on the reviewed release commit in CI.
- **Configured**: implementation exists, but the exact release/deployment has not supplied the required evidence.
- **Pending candidate**: requires the clean, committed, signed/notarized release candidate.
- **Pending external**: requires publisher-owned infrastructure, credentials, review, or accountable human evidence.
- **Not yet evidenced**: no result for the current worktree is recorded here.

Only a clean, reviewed exact SHA can be a production candidate. `scripts/release-macos.sh` intentionally refuses a distributable notarized build from a dirty worktree.

This ledger does not alter or approve the user-owned UI. Visual polish and accessibility acceptance remain exact-candidate owner evidence.

## Requirements 1–9

| # | Requirement | Authoritative proof | Current status |
|---:|---|---|---|
| 1 | No-AI, Gmail-like native mail scope | Backend/native contract tests; `AI_GROUPING_ENABLED=false`, `OPENAI_REQUIRED=false`, `OPENAI_DEBUG_LOGS=false`, empty `OPENAI_API_KEY`; exact-candidate manual matrix | **Passed (local)** automated no-AI/config contracts as part of the backend suite; production configuration is fail-closed in `deploy/production.env.example`. Exact-candidate Gmail parity remains manual evidence |
| 2 | Reproducible dependencies and toolchains | `npm ci`; `npm run reproducibility:verify`; reviewed lock diff | **Passed for private Beta 7 source SHA `835af4622c38e80fddac5d0c73b237b4e62a3f2f`, 2026-07-24:** an isolated clean tree passed `npm ci`, `npm ls --all`, hash-only Python installation, and the full verifier under exact Node 22.22.0, npm 10.9.4, and Python 3.12.13. Hosted run [30090612860](https://github.com/yednapg/electronic-mail/actions/runs/30090612860) separately passed the reproducibility job and the available Python 3.12.10 macOS target. The final production release SHA must repeat this gate |
| 3 | CI, security, and immutable build policy | Successful `Quality and release gates` push run for the exact release SHA; npm and Python audits; container jobs | **Passed for private Beta 7 source SHA `835af4622c38e80fddac5d0c73b237b4e62a3f2f`, 2026-07-24:** pull-request run [30090612860](https://github.com/yednapg/electronic-mail/actions/runs/30090612860) passed all six backend, web, macOS, reproducibility, and container jobs, including fresh dependency audits. A final `main` push run for the signed production release SHA is still required |
| 4 | Production topology, secrets, migrations, and readiness | `deploy/production.env.example`; Railway config-as-code; `npm run deploy:check`; `scripts/migrate-production.sh`; migrated `/ready` | Configured; production startup now rejects database connections without hostname-authenticated TLS and a trusted CA. Actual production services, secret values, migration execution, replica topology, edge policy, and exact `/ready` response are **Pending external** |
| 5 | Backup, restore, and disaster recovery | Managed PITR configuration; `npm run database:scripts:test`; authenticated encrypted logical backup; isolated restore drill record | **Passed (local)** for deterministic authenticated encryption, credential isolation, exact endpoint binding, concurrent-restore exclusion, atomic rollback, retention, and restore contracts. A disposable PostgreSQL 17.10 drill also completed a real encrypted custom-format restore, verified data, rejected a second non-pristine restore, re-enabled connections, and leaked no restore session. Actual managed PITR, off-host encrypted copies/key custody, and quarterly production-like isolated restore drills remain **Pending external** |
| 6 | Universal signed/notarized macOS artifact | Approved Xcode 16.4 (16F6); `npm run release:macos:preflight`; protected release workflow; `scripts/verify-macos-release.sh`; notarization/stapling/Gatekeeper evidence | Hosted Xcode 16.4 (16F6) preflight passed for Beta 7 source SHA `835af4622c38e80fddac5d0c73b237b4e62a3f2f`. Universal, ad-hoc-signed, unnotarized private [Beta 7](https://github.com/yednapg/electronic-mail/releases/tag/v0.1.0-beta.7) also passed static and mounted-DMG launch verification. Production remains pending Apple publisher credentials, Developer ID signing, notarization, stapling, and Gatekeeper evidence |
| 7 | Public landing, exact DMG download, privacy, terms, support | local web tests/build/assets; deployed `scripts/verify-public-pages.sh` with `EXPECTED_DMG_SHA256` | **Passed (local)** for source/build checks. Owned production domain, exact public DMG, monitored support/status endpoints, and legal approval are **Pending external** |
| 8 | Operations, observation, incident response, rollback | `/health`, `/ready`, authenticated `/v1/ops/health`; alerts; backup; rollback/incident drill | **Passed (local)** for HTTP 200 `/health` and `/ready` checks and revoked-Google-credential handling without a worker/poller retry storm. Production workers/queues/release parity, edge alerts, on-call roster, and drills are **Pending external** |
| 9 | Exact-artifact acceptance and go/no-go | Completed `docs/launch-acceptance.template.json`; immutable evidence objects; five approvals; production `npm run launch:verify` | Pending candidate and accountable manual/external evidence |

## Automated gates

| Gate | Command/evidence | Current status |
|---|---|---|
| Reproducibility policy | `npm run reproducibility:verify:test && npm run reproducibility:verify` | **Passed (local), 2026-07-24:** fail-closed manifest, lock, installed-graph, canonical-integrity, runner-specific runtime, YAML/container, and target-wheel policies pass. The full verifier passed in an isolated tree under exact Node 22.22.0, npm 10.9.4, and Python 3.12.13. Hash-only binary resolution proved all 53 runtime and 29 audit-tool pins for CPython 3.12/macOS Intel, and a clean Python 3.12.13 arm64 install passed `pip check` and backend import with `cryptography==48.0.1` |
| Lockfile install | Node 22.22.0: `npm ci` | **Passed (local), 2026-07-24:** isolated clean install added 36 packages, audited 39, and reported zero vulnerabilities |
| Installed production dependency graph | Canonical Node 22.22.0/npm 10.9.4 `npm ci`; lock/override verification; production build/runtime | **Passed (local), 2026-07-24:** clean canonical `npm ci`, `npm ls --all`, lock/override verification, and production build/runtime passed. Exact-SHA hosted evidence remains pending |
| Native compile/tests | `npm run macos:typecheck && npm run macos:test` | **Passed for Beta 7 source SHA `835af4622c38e80fddac5d0c73b237b4e62a3f2f`: 225/225 tests, 0 failures, 2026-07-24.** Local xcresult: `/tmp/ElectronicMailDerivedData/Logs/Test/Test-ElectronicMail-2026.07.24_17-13-31-+0530.xcresult`; hosted Xcode 16.4 evidence: run [30090612860](https://github.com/yednapg/electronic-mail/actions/runs/30090612860) |
| Universal unsigned policy preflight | Xcode 16.4 (16F6): `VERSION=1.0.0 BUILD_NUMBER=1 BACKEND_URL=https://api.electronicmail.app npm run release:macos:preflight` | **Passed for Beta 7 source SHA `835af4622c38e80fddac5d0c73b237b4e62a3f2f` under hosted Xcode 16.4 (16F6), 2026-07-24:** run [30090612860](https://github.com/yednapg/electronic-mail/actions/runs/30090612860). The private Beta 7 DMG was separately built with local Xcode beta and is not the production artifact |
| Backend unit/integration tests | `npm run backend:test`; `APP_ENV=staging DATABASE_URL=postgresql://localhost/electronic_mail npm run backend:test` | **Passed (local), 2026-07-24:** canonical Python 3.12.13 passed 440 tests with 20 staging-only skips in default mode and 440/440 against PostgreSQL 17.10 |
| Local runtime readiness/auth-failure behavior | `bash scripts/dev-backend-runtime.sh`; HTTP checks for `/health` and `/ready`; worker/poller observation with a revoked Google refresh credential; Computer Use smoke of the rebuilt native app | **Partially passed (local), 2026-07-24:** `/health` and `/ready` returned HTTP 200 with AI disabled and schema revision/head `20260723_0025`; credential expiry remained reauthorization-required without a retry storm. The latest native UI received a signed-in mailbox/navigation smoke before the local session expired. The prior release-mode scrolling captures predate the final UI rewrite and must be repeated on the exact candidate; a complete signed-in Beta 7 search/performance/accessibility smoke remains pending |
| Backend bytecode/dependency consistency | `npm run backend:compile && .venv/bin/pip check` | **Passed (local), 2026-07-24** |
| Deployment configuration guard | Production-shaped CI-valued `npm run deploy:check` | **Passed (local), 2026-07-23**; real production secret/origin values remain external evidence |
| Migration | `alembic heads`; local Postgres revision; `scripts/migrate-production.sh`; exact `/ready` schema head | **Passed (local):** one linear migration head and the local staging database are both at `20260723_0025`. Execution against production Postgres and live `/ready` remain pending external evidence |
| Database script contracts | `npm run database:scripts:test`; disposable PostgreSQL 17.10 E2E | **Passed (local), 2026-07-24**, including exact backup/restore endpoint binding, PostgreSQL 17.10-or-newer 17.x client/server policy, authenticated TLS/GSS policy, inherited trust-variable scrubbing, connection isolation, concurrent-restore exclusion, encrypted streaming, fail-closed pristine-target proof, atomic rollback, commit acknowledgment, connection re-enablement, and a real custom-format restore/data verification drill |
| Web typecheck | `npm run web:typecheck` | **Passed (local), 2026-07-24** |
| Web unit/contract tests | `npm run web:test` | **Passed (local): 34/34, 2026-07-24**, including safe `/post-login` behavior when the API is unreachable |
| Web production build/runtime boundary | `npm run web:build`; live standalone smoke | **Passed (local), 2026-07-24:** production build succeeded; with the backend unreachable, the standalone server returned the safe `/post-login` fallback with HTTP 200, while `/gmail`, `/dashboard`, and `/api/mailbox` returned 404 |
| Web font/deploy-tree policy | `npm run web:verify:assets:test && npm run web:verify:assets` | **Passed (local): 10/10 verifier tests and source-tree scan, 2026-07-23** |
| Launch verifier contract | `npm run launch:verify:test` | **Passed (local): 15/15 Python verifier tests plus fail-closed shell suite, 2026-07-24** |
| Beta distribution policy | `npm run release:macos:beta:test` | **Passed, 2026-07-24:** 31/31 policy, coinstallation-safety, exact-DMG-layout, exact-SHA publishing, byte-verification, and fixed-`github.com` routing tests passed. Private [Beta 7](https://github.com/yednapg/electronic-mail/releases/tag/v0.1.0-beta.7) was built from exact green SHA `835af4622c38e80fddac5d0c73b237b4e62a3f2f`; its universal mounted DMG passed both launch smokes and was published only after draft-asset and source-commit verification. DMG SHA-256: `4d78006ea597d6a7683a1b44a690025a3bf1590b39d9e2eba0e332e7144b2ff0`. It targets `http://localhost:3001`, so it is a private local-backend test build rather than a standalone remote beta |
| JavaScript production audit | `npm audit --omit=dev --audit-level=high` | **Passed (local), 2026-07-24:** registry-backed audit reported zero vulnerabilities |
| Python production audit | isolated hash-locked `pip-audit --strict --no-deps --disable-pip` for both Python locks | **Passed (local), 2026-07-24:** runtime and audit-tool locks reported no known vulnerabilities |
| Backend container build/readiness | Exact `backend/Dockerfile` image, migrated Postgres, `/ready` assertions | **Passed in hosted run [30090612860](https://github.com/yednapg/electronic-mail/actions/runs/30090612860) for private Beta 7 source SHA `835af4622c38e80fddac5d0c73b237b4e62a3f2f`** |
| Web container/runtime boundary | Exact `Dockerfile.web` image, legal pages, headers, native-only 404 boundary | **Passed in hosted run [30090612860](https://github.com/yednapg/electronic-mail/actions/runs/30090612860) for private Beta 7 source SHA `835af4622c38e80fddac5d0c73b237b4e62a3f2f`** |
| Exact-commit hosted CI | Green `.github/workflows/quality.yml` run for release SHA | **Passed for private Beta 7 source SHA `835af4622c38e80fddac5d0c73b237b4e62a3f2f`: all six jobs passed in pull-request run [30090612860](https://github.com/yednapg/electronic-mail/actions/runs/30090612860).** The final production release SHA still requires a successful `main` push run. Repository Actions require full-length SHA pins |
| Signed/notarized archive | Protected `.github/workflows/release-macos.yml` run | Pending candidate and Apple credentials |
| Full production verification | Production-valued `npm run launch:verify` | Pending all live, artifact, acceptance, and approval inputs |
| Patch hygiene | `git diff --check` | **Passed (local), 2026-07-24** |

## Repository publication risks

These items do not invalidate the verified local engineering gates above, but they must be resolved before making the repository public or treating it as a publishable source artifact:

- `backend/test.sqlite3` has been removed from the current tree, but the database remains reachable in existing Git history and contains real-looking mailbox metadata. No destructive history rewrite was performed. The repository owner must decide on history remediation and coordinate any required credential/data response before public release.
- The tracked, currently unused `web/font/SF-Pro-Rounded-*.otf` files total approximately 53 MB. Their provenance and redistribution rights are not evidenced. They are excluded from the verified web deployment tree, but removal, replacement, or documented licensing requires an owner/legal decision. This ledger update intentionally leaves those UI assets untouched.

## External release blockers

The following are not implied by any local pass and remain required before launch:

- A successful `main` push CI run for the final signed production release SHA, including fresh npm and Python vulnerability audits and container gates. Private Beta 7 passed the pull-request gate for its exact source SHA.
- Production URLs, secrets, TLS/edge policy, Postgres migration/readiness, workers, queues, monitoring, and release parity.
- Managed PITR, encrypted off-site backups, and recorded isolated restore and incident/rollback drills.
- Google OAuth production verification/security approval, consent/scope/domain evidence, and Pub/Sub authentication.
- Privacy/legal approval plus monitored support, status, and on-call ownership.
- Approved stable public Xcode 16.4 (build 16F6), Developer ID signing, notarization/stapling, Gatekeeper validation, and the clean-Mac install/upgrade matrix for the exact DMG.
- Product-owner visual polish acceptance and accountable accessibility evidence for the unchanged user-owned UI.

## Manual launch matrix

Every row below must be `pass` in the exact-DMG-bound acceptance manifest, with an accountable tester, time-zoned timestamp after DMG creation, redacted test-account label, macOS environment, and unique immutable evidence URL plus SHA-256. Repository automation cannot manufacture this evidence.

| Test ID | Scope | Current status |
|---|---|---|
| `install` | Clean Mac DMG install and Gatekeeper | Pending candidate |
| `auth` | First sign-in, cancel/retry, revocation, expiry, sign-out/relaunch | Pending candidate |
| `full_sync` | Oldest mail plus Inbox/Sent/Drafts/Spam/Trash/Archive counts and order | Pending candidate |
| `compose` | Addressing, body, attachment, retry, deduplication, receipt | Pending candidate |
| `draft` | Autosave, quit/relaunch, edit, attachment, discard, send | Pending candidate |
| `conversation` | Reply, Reply All, Forward, threading and quoting | Pending candidate |
| `actions` | Read/star/archive/trash/delete/spam operations in both directions | Pending candidate |
| `search` | Sender/recipient/subject/body plus clear, empty, and error states | Pending candidate |
| `attachments` | Download/save/open/upload under App Sandbox | Pending candidate |
| `sync` | Gmail-to-app/app-to-Gmail, sleep/wake, reconnect | Pending candidate |
| `scale` | Hundreds of rows, 50+ message thread, backfill, pagination, performance | Pending candidate |
| `failure` | Offline, API/worker/DB/Gmail faults and recovery | Pending candidate |
| `security` | Abuse limits, isolation, deletion, content-safe logs | Pending candidate |
| `accessibility` | Keyboard, VoiceOver, focus, contrast, text scaling | Pending candidate; visual acceptance remains user-owned |
| `update` | Signed upgrade without session/cache loss | Pending candidate and previous signed build |

## Accountable approvals and publisher-owned gates

| Gate | Required evidence | Current status |
|---|---|---|
| Product owner | `product_owner` approval bound to exact SHA/version/build/DMG/origins | Pending external |
| Privacy/legal | `privacy_legal` approval; production data, retention, regions, processors, Apple privacy declarations and published copy reconciled | Pending external |
| Google OAuth/security | `google_oauth_security` approval; verified production consent screen/scopes/domain, Production publishing status, Pub/Sub authentication, assessment or exemption | Pending external |
| Support/on-call | `support_on_call` approval; monitored owned-domain email/status page, roster, targets, articles and incident path | Pending external |
| Production operations | `production_operations` approval; services, migrations, backups, alerts, rollback, queues and exact releases verified | Pending external |
| Apple publisher | Developer ID identity/team, notarization credential, protected workflow approval | Pending external |
| Public distribution | Owned HTTPS web/API/evidence/download origins and exact approved DMG bytes | Pending external |

## Release evidence package

Do not publish until one directory/archive record contains the exact DMG, final app ZIP, `.xcarchive`, dSYMs, notarization submission results and logs, `RELEASE-METADATA-<version>-<build>.json`, `SHA256SUMS-<version>-<build>.txt`, source commit, completed acceptance manifest, and approval records. GitHub's 90-day private artifact is review transport, not the durable publisher archive.

The final go/no-go command is the production invocation documented in `docs/PRODUCTION_RUNBOOK.md`. Production mode rejects every skip and insecure override, checks the live API/workers/queues, deeply verifies both supplied and mounted apps, downloads every evidence record, verifies all approvals, checks public legal/support surfaces, and byte-compares the served DMG by SHA-256.
