# Stable local macOS installation without a paid Apple account

This lane replaces `/Applications/Electronic Mail.app` with a build signed by one persistent, self-signed certificate from the Mac's login Keychain. It is for private use on that Mac only. It does not provide Developer ID distribution, notarization, or public Gatekeeper trust.

## Why the certificate must remain stable

The local build stores session and offline-cache keys in the non-synchronizing classic macOS Keychain. The Keychain authorizes code using its signing requirement. Reusing the same certificate and the `app.electronicmail.mac` bundle identifier gives later local builds the same identity.

This private lane is intentionally not App Sandbox signed. A self-signed identity has no Apple Team identifier, so sandboxed Keychain access cannot provide the stable access group used by paid Apple signing. Its only entitlement disables hardened-runtime library validation so the app can load its separately signed embedded framework without a Team identifier. Production and distributable beta builds keep their separate sandbox policies unchanged.

The first self-signed installation may require one Google sign-in because a previous ad-hoc build had a build-specific identity. Subsequent replacements made through this lane retain the stable local identity.

## Create the identity once

In Keychain Access, select the `login` Keychain and choose **Keychain Access → Certificate Assistant → Create a Certificate…**. Use:

- Name: `Electronic Mail Local Signing`
- Identity Type: `Self Signed Root`
- Certificate Type: `Code Signing`

Set the certificate's Code Signing trust to **Always Trust**, then confirm it is available with:

```bash
security find-identity -v -p codesigning
```

The certificate must appear with its private key and as exactly one valid identity. Back up the certificate and private key securely; deleting or replacing them changes the app identity and may require signing in again.

## Validate and install

Start the backend at `http://localhost:3001`, then run:

```bash
npm run macos:local-self-signed:validate
npm run macos:local-self-signed:install
```

To use a differently named local certificate:

```bash
LOCAL_CODE_SIGN_IDENTITY='Your Local Code Signing Name' \
npm run macos:local-self-signed:install
```

The installer verifies the exact identity, backend readiness, universal architectures, bundle identifier, classic-Keychain compilation policy, the local unsandboxed entitlement policy, non-ad-hoc signature, and designated requirement before replacing the installed app. It keeps a temporary copy of the prior app and restores it if post-install verification or launch fails. It does not delete the app container, Keychain records, Gmail accounts, local mailbox cache, or backend database.

## Boundaries

- Do not distribute this build to other Macs.
- Do not delete or regenerate the local certificate between builds.
- Do not use the paid Developer ID installer with this certificate.
- Public distribution must continue to use the separate Developer ID and notarization workflow.
