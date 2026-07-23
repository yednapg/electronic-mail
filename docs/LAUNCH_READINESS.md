# Launch readiness ledger

This is the fail-closed status ledger for the no-AI Electronic Mail macOS launch. It maps the repository's launch requirements to the command or external record that proves each one. A configured gate is not a passed gate. Update this file only from the named evidence; never infer completion from implementation intent.

Status vocabulary:

- **Passed (local)**: the named deterministic command passed in the current worktree on 2026-07-23. It must pass again on the reviewed release commit in CI.
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
| 2 | Reproducible dependencies and toolchains | `npm ci`; `npm run reproducibility:verify`; reviewed lock diff | **Configured and fail-closed:** static manifest/lock/container/workflow policy checks pass, but the full gate correctly rejects this host's Node 26.4.0/Python 3.14.6 and local Python 3.14 virtualenv instead of claiming the pinned Node 22.22.0/Python 3.12.13 runtimes. Canonical-runtime CI evidence and a hash-locked Python artifact set remain pending |
| 3 | CI, security, and immutable build policy | Successful `Quality and release gates` push run for the exact release SHA; npm and Python audits; container jobs | Configured; local policy/web/database/launch-verifier checks passed, but no exact-commit hosted CI run exists for uncommitted changes and fresh registry-backed npm and Python audits remain pending |
| 4 | Production topology, secrets, migrations, and readiness | `deploy/production.env.example`; Railway config-as-code; `npm run deploy:check`; `scripts/migrate-production.sh`; migrated `/ready` | Configured; actual production services, secret values, migration execution, replica topology, TLS/edge policy, and exact `/ready` response are **Pending external** |
| 5 | Backup, restore, and disaster recovery | Managed PITR configuration; `npm run database:scripts:test`; authenticated encrypted logical backup; isolated restore drill record | **Passed (local)** for deterministic authenticated-encryption, credential-isolation, retention, and restore contracts. Actual managed PITR, off-host encrypted copies/key custody, and quarterly isolated restore drill are **Pending external** |
| 6 | Universal signed/notarized macOS artifact | Approved Xcode 16.4 (16F6); `npm run release:macos:preflight`; protected release workflow; `scripts/verify-macos-release.sh`; notarization/stapling/Gatekeeper evidence | Pending approved-toolchain candidate and Apple publisher credentials |
| 7 | Public landing, exact DMG download, privacy, terms, support | local web tests/build/assets; deployed `scripts/verify-public-pages.sh` with `EXPECTED_DMG_SHA256` | **Passed (local)** for source/build checks. Owned production domain, exact public DMG, monitored support/status endpoints, and legal approval are **Pending external** |
| 8 | Operations, observation, incident response, rollback | `/health`, `/ready`, authenticated `/v1/ops/health`; alerts; backup; rollback/incident drill | **Passed (local)** for HTTP 200 `/health` and `/ready` checks and revoked-Google-credential handling without a worker/poller retry storm. Production workers/queues/release parity, edge alerts, on-call roster, and drills are **Pending external** |
| 9 | Exact-artifact acceptance and go/no-go | Completed `docs/launch-acceptance.template.json`; immutable evidence objects; five approvals; production `npm run launch:verify` | Pending candidate and accountable manual/external evidence |

## Automated gates

| Gate | Command/evidence | Current status |
|---|---|---|
| Reproducibility policy | `npm run reproducibility:verify:test && npm run reproducibility:verify` | **Partially passed (local), 2026-07-23:** 50/50 fail-closed policy tests pass, including manifest-to-lock, installed-graph, canonical integrity, per-step workflow-runtime, and YAML/container-parser policy. The full verifier correctly fails before success because this host and `.venv` do not match the pinned Node 22.22.0/Python 3.12.13 runtimes |
| Lockfile install | Node 22.22.0: `npm ci` | Pending canonical Node 22.22.0 CI evidence; the local host is Node 26 and is now correctly rejected by `engine-strict=true` |
| Installed production dependency graph | `npm ls --all --omit=dev` | **Passed (local), 2026-07-23**; platform-specific unmet optional packages are expected |
| Native compile/tests | `npm run macos:typecheck && npm run macos:test` | **Passed (local): test build succeeded; 211/211 tests, 0 failures, 2026-07-23.** xcresult: `/tmp/ElectronicMailDerivedData/Logs/Test/Test-ElectronicMail-2026.07.23_21-25-12-+0530.xcresult` |
| Universal unsigned policy preflight | Xcode 16.4 (16F6): `VERSION=1.0.0 BUILD_NUMBER=1 BACKEND_URL=https://api.electronicmail.app npm run release:macos:preflight` | **Not yet evidenced under the approved toolchain.** The prior local universal identity-free build used Xcode beta and no longer satisfies this exact-toolchain gate; only Xcode beta is installed locally |
| Backend unit/integration tests | `npm run backend:test`; `APP_ENV=staging DATABASE_URL=postgresql://localhost/electronic_mail npm run backend:test` | **Passed (local), 2026-07-23:** the default runner passed 331 tests with 17 staging-only skips; the staging/Postgres run passed 331 tests with 0 skips |
| Local runtime readiness/auth-failure behavior | `bash scripts/dev-backend-runtime.sh`; HTTP checks for `/health` and `/ready`; worker/poller observation with a revoked Google refresh credential; Computer Use smoke of the rebuilt native app | **Passed (local), 2026-07-23:** `/health` and `/ready` returned HTTP 200 with AI disabled and the current schema head; credential expiry remained reauthorization-required without a retry storm. The rebuilt signed-in app relaunched without losing its session, defaulted to a hidden sidebar, rendered a known read message from a 5.12 ms reader response, showed/hid its sidebar with one bounded count sweep, and completed/cleared native search from a 103.75 ms response |
| Backend bytecode/dependency consistency | `npm run backend:compile && .venv/bin/pip check` | **Passed (local), 2026-07-23** |
| Deployment configuration guard | Production-shaped CI-valued `npm run deploy:check` | **Passed (local), 2026-07-23**; real production secret/origin values remain external evidence |
| Migration | `alembic heads`; local Postgres revision; `scripts/migrate-production.sh`; exact `/ready` schema head | **Passed (local):** one linear migration head and the local staging database are both at `20260723_0025`. Execution against production Postgres and live `/ready` remain pending external evidence |
| Database script contracts | `npm run database:scripts:test` | **Passed (local), 2026-07-23** |
| Web typecheck | `npm run web:typecheck` | **Passed (local), 2026-07-23** |
| Web unit/contract tests | `npm run web:test` | **Passed (local): 34/34, 2026-07-23**, including safe `/post-login` behavior when the API is unreachable |
| Web production build/runtime boundary | `npm run web:build`; live standalone smoke | **Passed (local), 2026-07-23:** production build succeeded; with the backend unreachable, the standalone server returned the safe `/post-login` fallback with HTTP 200, while `/gmail`, `/dashboard`, and `/api/mailbox` returned 404 |
| Web font/deploy-tree policy | `npm run web:verify:assets:test && npm run web:verify:assets` | **Passed (local): 10/10 verifier tests and source-tree scan, 2026-07-23** |
| Launch verifier contract | `npm run launch:verify:test` | **Passed (local): 7/7 manifest tests plus fail-closed shell suite, 2026-07-23** |
| JavaScript production audit | `npm audit --omit=dev --audit-level=high` | **Not yet evidenced for the current dependency graph:** a fresh registry-backed audit remains pending because network approval was not granted in this environment. No offline-cache result is claimed |
| Python production audit | `python -m pip_audit -r backend/requirements.lock` | **Not yet evidenced:** registry/advisory access and dependency-metadata disclosure have not been approved in this environment |
| Backend container build/readiness | Exact `backend/Dockerfile` image, migrated Postgres, `/ready` assertions | Configured in CI; not run locally in this ledger |
| Web container/runtime boundary | Exact `Dockerfile.web` image, legal pages, headers, native-only 404 boundary | Configured in CI; not run locally in this ledger |
| Exact-commit hosted CI | Green `.github/workflows/quality.yml` push run for release SHA | Pending exact-SHA push and hosted CI |
| Signed/notarized archive | Protected `.github/workflows/release-macos.yml` run | Pending candidate and Apple credentials |
| Full production verification | Production-valued `npm run launch:verify` | Pending all live, artifact, acceptance, and approval inputs |
| Patch hygiene | `git diff --check` | **Passed (local), 2026-07-23** |

## Repository publication risks

These items do not invalidate the verified local engineering gates above, but they must be resolved before making the repository public or treating it as a publishable source artifact:

- `backend/test.sqlite3` has been removed from the current tree, but the database remains reachable in existing Git history and contains real-looking mailbox metadata. No destructive history rewrite was performed. The repository owner must decide on history remediation and coordinate any required credential/data response before public release.
- The tracked, currently unused `web/font/SF-Pro-Rounded-*.otf` files total approximately 53 MB. Their provenance and redistribution rights are not evidenced. They are excluded from the verified web deployment tree, but removal, replacement, or documented licensing requires an owner/legal decision. This ledger update intentionally leaves those UI assets untouched.

## External release blockers

The following are not implied by any local pass and remain required before launch:

- A successful hosted CI run for the exact reviewed release SHA, including fresh npm and Python vulnerability audits and container gates.
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
