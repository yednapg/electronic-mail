# Electronic Mail

Electronic Mail is a focused native macOS Gmail client. The launch build provides standard mail workflows—sign-in, mailbox folders, full synchronization, reading and search, compose/reply/forward, drafts, attachments, and Gmail actions—without AI features. The web service is limited to the native download page, OAuth completion, privacy, terms, and support; it is not a second email client.

## Local development

Requirements are Node 22.22.0, npm 10 or 11, Python 3.12.13, full Xcode, and local Postgres. Then run:

```bash
npm run setup
npm run backend:dev
npm run dev:macos
```

See `macos/ElectronicMail/README.md` for native build details. Never use production Google, database, signing, or notarization credentials in local `.env` files.

## Verification

```bash
npm run reproducibility:verify:test
npm run reproducibility:verify
npm run verify:all
npm run release:macos:preflight
```

`verify:all` is a source gate, not approval to distribute. A public build additionally requires a clean reviewed commit, the protected Developer ID/notarization workflow, a migrated healthy production deployment, the exact-artifact manual matrix, publisher/legal/security/support/operations approvals, and the fail-closed production launch verifier.

## Launch documents

- `docs/LAUNCH_READINESS.md` — current evidence and unresolved gates
- `docs/PRODUCTION_RUNBOOK.md` — topology, deployment, health, backup, restore, rollback
- `docs/MACOS_RELEASE.md` — signing, notarization, packaging, artifact verification
- `docs/LAUNCH_TEST_MATRIX.md` — required clean-Mac and Gmail parity evidence
- `docs/PRIVACY_SUPPORT_CHECKLIST.md` — publisher, OAuth, privacy, and support gates
- `SECURITY.md` — private vulnerability reporting policy
