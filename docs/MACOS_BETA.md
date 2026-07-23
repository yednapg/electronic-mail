# macOS local-testing beta DMG

This path creates a universal optimized macOS beta for trusted testers before Apple distribution credentials are available. It is deliberately separate from the production release pipeline.

The resulting app is ad-hoc signed and **unnotarized**. It is not a production release, it does not claim Gatekeeper acceptance, and it must be published only as a GitHub **prerelease**. `scripts/release-macos.sh` remains the only production path and still requires Developer ID signing, notarization, stapling, and exact-toolchain verification.

## Choose the backend

The default is the backend running on the same Mac as the app:

```bash
BACKEND_URL=http://localhost:3001
```

That default is appropriate for testing the DMG locally. A GitHub tester on another Mac cannot reach your localhost. For remote testers, first deploy a test backend at a canonical public HTTPS origin, then bind that exact origin into the app:

```bash
BACKEND_URL=https://api-beta.your-owned-domain.com
```

The beta rejects every other insecure URL, private/reserved hosts, placeholder domains, credentials, paths, queries, fragments, noncanonical hostnames, and an explicit default HTTPS port. Its dedicated Info.plist contains only the exact `localhost` insecure-HTTP ATS exception needed by the local default. It contains no arbitrary-load or broad local-network exception.

## Validate and package

Choose a numeric Apple bundle version and build number. The packaging command requires a completely clean, committed source tree so the app, metadata, README, and release notes all bind to one full source SHA.

```bash
VERSION=0.1.0 \
BUILD_NUMBER=1 \
BACKEND_URL=http://localhost:3001 \
npm run release:macos:beta:validate

npm run release:macos:beta:test

VERSION=0.1.0 \
BUILD_NUMBER=1 \
BACKEND_URL=http://localhost:3001 \
npm run release:macos:beta
```

No Developer ID identity or notarization credential is read. Any selected full Xcode can build this local-testing artifact; set `DEVELOPER_DIR` if needed. The script uses the Xcode Release configuration, forces `arm64` and `x86_64`, applies explicit ad-hoc signatures, and enables a local-beta-only non-synchronizing classic Keychain fallback because an identity-free app has no Apple team entitlement. The fallback flag is never supplied by production preflight or release scripts.

The DMG contains exactly:

- `ElectronicMail.app`
- an `Applications` symlink
- `README-BETA.txt`, with the unnotarized warning and exact build binding

Before returning success, the script verifies the compressed DMG, mounts it read-only, checks its exact contents, verifies both architectures, all bound Info.plist values, both privacy-manifest copies, strict ad-hoc signatures and entitlements, and then re-verifies the checksum manifest.

Artifacts are written to `artifacts/macos-beta/`:

- `ElectronicMail-Beta-<version>-<build>.dmg`
- `ElectronicMail-Beta-<version>-<build>-metadata.json`
- `ElectronicMail-Beta-<version>-<build>-release-notes.md`
- `ElectronicMail-Beta-<version>-<build>-SHA256SUMS.txt`

## Publish as a GitHub prerelease

Review the generated metadata and notes first. Then use the generated notes and attach all four files. This example creates a prerelease; it does not mark the build as latest or production-ready:

```bash
VERSION=0.1.0
BUILD_NUMBER=1
STEM="ElectronicMail-Beta-$VERSION-$BUILD_NUMBER"

gh release create "v$VERSION-beta.$BUILD_NUMBER" \
  --prerelease \
  --title "Electronic Mail $VERSION ($BUILD_NUMBER) beta" \
  --notes-file "artifacts/macos-beta/$STEM-release-notes.md" \
  "artifacts/macos-beta/$STEM.dmg" \
  "artifacts/macos-beta/$STEM-metadata.json" \
  "artifacts/macos-beta/$STEM-release-notes.md" \
  "artifacts/macos-beta/$STEM-SHA256SUMS.txt"
```

Tell testers to use a dedicated Gmail account. For localhost builds, start the backend before opening the app. macOS may block an unnotarized download or require an explicit tester override; this beta path intentionally does not run `spctl` or claim that Gatekeeper will accept it. Record issues against the exact source SHA and DMG checksum in the metadata.
