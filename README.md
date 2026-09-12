# Electronic Mail

Electronic Mail is a focused native macOS Gmail client. It keeps the normal Gmail Inbox intact and offers an optional, separately cached AI Inbox that organizes related messages into backend-owned matters. Standard mail workflows remain available without AI, and every Gmail action reconciles into both views. The web service is limited to the native download page, OAuth completion, privacy, terms, and support; it is not a second email client.

## Private source beta

Trusted testers build and run the app locally; this path does not require TestFlight, an Apple Developer membership, Railway, signing, or notarization. After installing the requirements below, use:

```bash
npm run beta:setup -- your-gmail-address@gmail.com
# Import or add your own Google OAuth web client credentials (see the guide).
npm run beta:check
npm run beta:start
```

The complete Google Cloud and troubleshooting instructions are in [`docs/PRIVATE_BETA.md`](docs/PRIVATE_BETA.md). Every tester uses their own Google Cloud test project and keeps its credentials out of Git.

## Local development

Requirements are Node 22.22.0, npm 10 or 11, Python 3.12.13, full Xcode, and local Postgres. Then run:

```bash
npm run setup
npm run backend:dev
npm run dev:macos
```

See `macos/ElectronicMail/README.md` for native build details. Never use production Google, database, signing, or notarization credentials in local `.env` files.

New-email push notifications for Mac and iPhone are opt-in under Settings → Notifications. See [push notification setup](docs/PUSH_NOTIFICATIONS.md) for Apple signing, backend configuration, and device verification.

### Local AI Inbox testing with Codex

Local manual testing can run AI Inbox classification and review through the signed-in Codex app instead of the usage-billed API. Install the local-only adapter and configure `backend/.env`:

```bash
./.venv/bin/python -m pip install -r backend/requirements.local-ai.txt

AI_INBOX_ENABLED=true
AI_INBOX_LOCAL_SHADOW_PREVIEW=true
AI_INBOX_TEXT_PROVIDER=codex
AI_INBOX_EMBEDDING_PROVIDER=local
AI_INBOX_CLASSIFIER_MODEL=gpt-5.6-sol
AI_INBOX_REVIEW_MODEL=gpt-5.6-sol
AI_INBOX_CODEX_SERVICE_TIER=fast
AI_INBOX_CODEX_TIMEOUT_SECONDS=90
AI_INBOX_EMBEDDING_MODEL=local-hash-v1
```

The local shadow-preview switch lets the normal AI Inbox tab display the newest local test generation without promoting it. The Codex path is deliberately local-only, uses a fresh ephemeral read-only turn for each decision, and requires ChatGPT authentication. Automated tests mock the provider and do not consume Codex or API usage. Staging and production reject this configuration and continue to require the OpenAI Responses and Embeddings APIs.

## Verification

```bash
npm run reproducibility:verify:test
npm run reproducibility:verify
npm run verify:all
npm run release:macos:preflight
```

For a clean-commit, universal ad-hoc DMG intended only for a GitHub prerelease test group, see `docs/MACOS_BETA.md` and run `npm run release:macos:beta:test` before `npm run release:macos:beta`.

`verify:all` is a source gate, not approval to distribute. A public build additionally requires a clean reviewed commit, the protected Developer ID/notarization workflow, a migrated healthy production deployment, the exact-artifact manual matrix, publisher/legal/security/support/operations approvals, and the fail-closed production launch verifier.

## Launch documents

- `docs/LAUNCH_READINESS.md` — current evidence and unresolved gates
- `docs/PRODUCTION_RUNBOOK.md` — topology, deployment, health, backup, restore, rollback
- `docs/MACOS_RELEASE.md` — signing, notarization, packaging, artifact verification
- `docs/LAUNCH_TEST_MATRIX.md` — required clean-Mac and Gmail parity evidence
- `docs/PRIVACY_SUPPORT_CHECKLIST.md` — publisher, OAuth, privacy, and support gates
- `SECURITY.md` — private vulnerability reporting policy
