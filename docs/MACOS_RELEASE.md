# macOS direct-distribution release

Electronic Mail ships as a native macOS app. The web project remains a supporting surface for OAuth completion and the public privacy, terms, and support pages; it is not the product UI.

In production, the web service returns `404` for `/gmail`, `/dashboard`, their nested routes, and the legacy web BFF under `/api`. The legacy UI remains reachable only in a non-production developer runtime for fixtures and regression tests. The public home page must direct users to the installed Mac app, and `/post-login` exists only to confirm a completed browser OAuth flow and send the user back to the app.

The public artifact must be a universal (`arm64` + `x86_64`) Developer ID-signed app with Hardened Runtime, App Sandbox, a production HTTPS backend origin, a valid privacy manifest, successful Apple notarization, a stapled ticket, and Gatekeeper acceptance.

## Credential-free preflight

Run this first on a Mac with the reviewed release toolchain: Xcode 16.4, build 16F6, installed at `/Applications/Xcode_16.4.app/Contents/Developer`. The approved version, build, and path live in `scripts/macos-release-toolchain.env`; both preflight and release fail closed on any mismatch. Preflight never reads a signing identity or notarization credential and produces only an explicitly non-distributable, identity-free app under `/tmp`. The linker may add an ad-hoc marker, but the verifier rejects any certificate authority or Apple team identity:

```bash
npm run release:macos:preflight
```

The preflight performs a clean optimized Release build, forces both Mac architectures, injects a harmless preflight HTTPS origin, and verifies:

- bundle identifier, version/build, minimum macOS 14, OAuth callback scheme, and expanded copyright year;
- exact backend URL injection and absence of Release ATS localhost/HTTP exceptions;
- App Sandbox, outbound network, and user-selected file entitlements in the source policy;
- app icon and unchanged app/framework copies of `PrivacyInfo.xcprivacy`, including account/profile identifiers, email content, attachments, search/interaction/diagnostic data, and the UserDefaults reason;
- both `arm64` and `x86_64` in the packaged app and framework executables, plus matching universal dSYM UUIDs.

Use a real candidate origin without contacting it by setting `BACKEND_URL`:

```bash
BACKEND_URL=https://api.your-company.com \
VERSION=1.0.0 BUILD_NUMBER=1 \
npm run release:macos:preflight
```

Passing preflight does **not** prove signing, notarization, Gatekeeper acceptance, OAuth, or Gmail parity.

## Privacy-manifest engineering coverage

The checked-in manifest is an intentionally conservative engineering baseline for the current native data paths. Every declared data type is linked to the signed-in account, is marked as not used for tracking, and has only the App Functionality purpose:

| Apple declaration | Native path covered |
| --- | --- |
| Name, Email Address, User ID | Google profile and Electronic Mail account/session identity |
| Emails or Text Messages | Message headers, participants, subjects, bodies, threads, drafts, replies, and sends |
| Photos or Videos, Audio Data, Other User Content | The unrestricted file-attachment picker, attachment upload/download, and non-media documents |
| Search History | Mailbox search terms sent to the service |
| Product Interaction | Mailbox actions, draft/send state, sync state, and other account-linked feature events |
| Other Diagnostic Data | Delivery, synchronization, and operational/security errors associated with the account |
| Other Data Types | OAuth authorization/session metadata and mailbox metadata that has no more specific Apple category |

The native source audit found one required-reason API category: app-private `UserDefaults`, declared with Apple reason `CA92.1`. It found no direct use of the file-timestamp, system-boot-time, disk-space, or active-keyboard required-reason API categories. Because both the app executable and the embedded `ElectronicMailCore` dynamic framework contain code that uses `UserDefaults`, the same reviewed manifest is packaged in both bundles. The release verifier requires exact, duplicate-free declarations and semantically equivalent plist content in both locations.

Review declarations against Apple's current [collected-data type definitions](https://developer.apple.com/documentation/bundleresources/app-privacy-configuration/nsprivacycollecteddatatypes/nsprivacycollecteddatatype), [App Store privacy guidance](https://developer.apple.com/app-store/app-privacy-details/), and [required-reason API rules](https://developer.apple.com/documentation/bundleresources/describing-use-of-required-reason-api) whenever data handling or the toolchain changes.

This is code-complete taxonomy **coverage**, not legal approval. Before release, the accountable privacy/legal owner must compare these conservative declarations with the production backend and every subprocessor, decide whether each off-device value is retained long enough to meet Apple's definition of collection, confirm purposes/linking/tracking answers, and reconcile production logs, IP handling, attachment types, deletion/backup retention, and the published privacy policy. If an App Store submission is planned, that review must also reconcile the App Store Connect privacy answers. Engineering automation cannot make or approve those facts.

Validate the final version/build/backend inputs without building or reading credentials:

```bash
VERSION=1.0.0 BUILD_NUMBER=1 \
BACKEND_URL=https://api.your-company.com \
npm run release:macos:validate
```

## One-time Apple setup

1. Enroll the legal publisher in the Apple Developer Program.
2. Register `app.electronicmail.mac` under that team.
3. Create and install a **Developer ID Application** certificate with its private key.
4. Install the approved stable public Xcode 16.4 (build 16F6) at `/Applications/Xcode_16.4.app`; beta, unversioned, newer, and older toolchains are rejected until the reviewed pin is deliberately updated.
5. Create App Store Connect notarization credentials and store them in Keychain:

```bash
xcrun notarytool store-credentials electronic-mail-notary \
  --apple-id 'release@example.com' \
  --team-id 'TEAMID' \
  --password 'APP-SPECIFIC-PASSWORD'
```

An App Store Connect API key is preferred for CI. Never commit the certificate, private key, API key, app-specific password, or Keychain export.

The Release verifier requires Xcode’s signed `com.apple.application-identifier` and team identifier. Apple documents that Xcode forms the application identifier from the team ID and bundle ID, which provides the app’s private default access group for Data Protection Keychain: [Apple Keychain access-group documentation](https://developer.apple.com/documentation/security/sharing-access-to-keychain-items-among-a-collection-of-apps). The app must not be distributed if this gate fails.

## Local production build

```bash
VERSION=1.0.0 \
BUILD_NUMBER=1 \
BACKEND_URL=https://api.your-company.com \
APPLE_DEVELOPMENT_TEAM=TEAMID1234 \
DEVELOPER_ID_APPLICATION='Developer ID Application: Publisher Name (TEAMID1234)' \
NOTARY_PROFILE=electronic-mail-notary \
npm run release:macos
```

The release script:

1. requires `/Applications/Xcode_16.4.app/Contents/Developer` to report exactly Xcode 16.4 build 16F6, and rejects beta or drifted toolchains, placeholder/local backend origins, missing credentials, missing signing identities, and a dirty production source tree;
2. creates a signed universal archive and requires matching universal dSYMs;
3. verifies Developer ID authority/team, Hardened Runtime, minimal sandbox entitlements, the team-bound application identifier, privacy manifest, icon, and injected configuration;
4. notarizes and staples the app;
5. creates and signs the DMG, then notarizes, staples, and Gatekeeper-assesses both deliverables;
6. packages the final ZIP only after the app is stapled, then reopens and fully verifies both the ZIP and mounted DMG contents;
7. requires clean notarization logs with no reported issues, records the exact approved Xcode version/build in release metadata, writes those logs, submission results, metadata, dSYMs, and the final archive under `artifacts/macos/`, then generates and immediately re-verifies the SHA-256 manifest.

`SKIP_NOTARIZATION=1` is only for debugging the signing/package pipeline. It writes to `artifacts/macos-local-unnotarized/`, adds `NOT_FOR_DISTRIBUTION.txt`, and must never be published.

## GitHub Actions release

The manually dispatched **Build notarized macOS release** workflow uses the protected `production-macos` environment. Configure an approval rule and these environment secrets:

- `APPLE_DEVELOPMENT_TEAM`
- `DEVELOPER_ID_APPLICATION`
- `MACOS_DEVELOPER_ID_P12_BASE64`
- `MACOS_DEVELOPER_ID_P12_PASSWORD`
- `APP_STORE_CONNECT_KEY_ID`
- `APP_STORE_CONNECT_ISSUER_ID`
- `APP_STORE_CONNECT_PRIVATE_KEY_BASE64`

The workflow only releases the reviewed `main` branch and requires a successful **Quality and release gates** push run for that exact commit. Quality, credential-free preflight, and protected release jobs all set the same versioned `DEVELOPER_DIR` and independently verify Xcode 16.4 build 16F6 before building. It validates the requested version/build/backend and runs native tests plus an exact-candidate credential-free preflight in a separate `macos-15` job. Only after those checks succeed does the protected release job gain access to signing/notarization credentials. The launch verifier rejects metadata from any other Xcode version/build. Release-job actions are pinned to reviewed commit hashes. The workflow uploads a private artifact for review with 90-day GitHub retention; it deliberately does not create a public GitHub Release or publish a download automatically. Before publication, copy the candidate, dSYMs, notarization records, checksums, metadata, and approval record into the publisher's access-controlled durable archive. GitHub artifact retention is not that archive.

## Debug versus Release Keychain policy

Locally ad-hoc-signed Debug builds lack Apple’s team-bound application identifier. They may use a non-synchronizing classic macOS Keychain fallback solely so developers can complete OAuth. Release builds compile that fallback out and require Data Protection Keychain with `WhenUnlockedThisDeviceOnly`. A Release app showing Keychain status `-34018` is incorrectly signed and must not ship.

## Final external acceptance

- Install the DMG on a clean macOS 14+ Mac that has never run a development build.
- Confirm Gatekeeper opens it without an override and the app shows the intended Finder/Dock icon.
- Confirm first OAuth, cancel/retry, relaunch session restoration, compose/reply/drafts/attachments, offline recovery, sign-out, disconnect, and account deletion.
- Compare Inbox, Sent, Drafts, Spam, Trash, Archive, search results, counts, and order against Gmail web.
- Verify the public privacy, terms, and support URLs and complete every row in `docs/LAUNCH_TEST_MATRIX.md` with named evidence.
- Copy `docs/launch-acceptance.template.json`, bind it to the exact source commit/version/build/backend/web origins, and record every required test plus the five named approvals. Production verification cannot skip or accept a partial manifest.
- Have the accountable privacy/legal owner confirm that the manifest categories still match the production backend, logging, retention, and attachment behavior before each release.
- Retain the archive, dSYMs, notarization IDs, checksums, source commit, and approval record.
