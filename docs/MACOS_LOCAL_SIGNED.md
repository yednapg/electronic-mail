# Stable local macOS installation

Use this lane to replace the local `/Applications/Electronic Mail.app` while keeping the production bundle identifier and a stable Apple Developer Team signing identity. It is intentionally separate from both the ad-hoc local beta and the notarized public release.

## Why this preserves sign-in

Data Protection Keychain access is bound to the signed app identity. Rebuilding with the same Developer ID Application identity, Team ID, and `app.electronicmail.mac` bundle identifier keeps that identity stable across later replacements.

The first stable build also performs one bounded migration for an existing ad-hoc build:

1. It reads only the exact generation selected by the nonsecret session ledger from the classic login Keychain.
2. It copies the same bytes into the Team-bound Data Protection Keychain.
3. It reads the new record back and verifies the bytes.
4. Only then does it delete that exact classic record.

If access, writing, or verification fails, the classic record is retained and launch shows **Try Again** and **Sign In Again**. No mailbox, Gmail account, OAuth account, cache, or backend data is deleted by this migration.

## Prerequisites

- Apple Developer Program membership with permission to create a Developer ID certificate.
- A `Developer ID Application` certificate and its private key in the login Keychain.
- The 10-character Apple Team ID.
- The local backend ready at `http://localhost:3001`.
- Full Xcode installed.

List available signing identities:

```bash
security find-identity -v -p codesigning
```

Validate inputs without building or replacing anything:

```bash
APPLE_DEVELOPMENT_TEAM=YOURTEAMID \
DEVELOPER_ID_APPLICATION='Developer ID Application: Your Name (YOURTEAMID)' \
npm run macos:local-signed:validate
```

Build, verify, replace, verify again, and open the app:

```bash
APPLE_DEVELOPMENT_TEAM=YOURTEAMID \
DEVELOPER_ID_APPLICATION='Developer ID Application: Your Name (YOURTEAMID)' \
npm run macos:local-signed:install
```

The installer fails before changing `/Applications` if the identity or backend is unavailable. During replacement it keeps a temporary copy of the previous app and restores it if post-install verification fails. App container data, Keychain records, and the backend database are not replaced.

## Security and distribution boundary

This lane is Developer-ID signed with Hardened Runtime and the production entitlement set, but it is deliberately not notarized and contains only the exact localhost HTTP transport exception needed for local development. Do not distribute this artifact. Use `npm run release:macos` for a public notarized release.

Apple references: [Create Developer ID certificates](https://developer.apple.com/help/account/certificates/create-developer-id-certificates), [Configure Keychain sharing](https://developer.apple.com/documentation/xcode/configuring-keychain-sharing), and [Create distribution-signed code for macOS](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac).
