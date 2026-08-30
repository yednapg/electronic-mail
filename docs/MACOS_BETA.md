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

Hardened Runtime remains enabled for the beta. An ad-hoc signature has no Apple Developer Team ID, so its embedded ad-hoc framework cannot satisfy Hardened Runtime's same-team library validation. The packaging path therefore applies a separate, exact beta entitlement set with `com.apple.security.cs.disable-library-validation` enabled. This is a narrow but meaningful security reduction: use the artifact only with trusted prerelease testers. The production entitlement file is unchanged, the production verifier explicitly forbids this exception, and the beta entitlement file is never referenced by the Xcode project, production preflight, or production release script.

The DMG contains exactly:

- `Electronic Mail Beta.app` with the separate `app.electronicmail.mac.beta` bundle identifier (this prevents an install overwrite; it does not isolate OAuth scheme routing)
- an `Applications` symlink
- `README-BETA.txt`, with the unnotarized warning and exact build binding

Before returning success, the script verifies the compressed DMG, mounts it read-only, checks its exact contents, verifies both architectures, all bound Info.plist values, both privacy-manifest copies, strict ad-hoc signatures and the exact beta entitlement set, and then re-verifies the checksum manifest. A private beta-only headless launch mode exits before creating stores or UI; the packager runs it once after signing and once from the mounted DMG so dynamic-linker policy failures cannot pass static signature verification.

Artifacts are written to `artifacts/macos-beta/`:

- `ElectronicMail-Beta-<version>-<build>.dmg`
- `ElectronicMail-Beta-<version>-<build>-metadata.json`
- `ElectronicMail-Beta-<version>-<build>-release-notes.md`
- `ElectronicMail-Beta-<version>-<build>-SHA256SUMS.txt`

## Publish as a GitHub prerelease

Do not call `gh release create` directly. When the requested tag does not already
exist, GitHub CLI otherwise creates it from the repository's default branch,
which can differ from the source commit recorded in the DMG metadata.

The repository publisher validates the local metadata and all three checksummed
files, requires the full metadata source commit to exist in the exact remote
repository, creates or verifies the remote tag at that commit, and creates only
a **draft prerelease** with `latest=false`. It then downloads all four draft
assets and verifies their names, sizes, and SHA-256 digests against the local
files. The source commit must already exist in that repository; this script
never pushes source or branches. The command returns with the release still
unpublished:

Every publisher operation is explicitly bound to `github.com`; an inherited
`GH_HOST` or GitHub Enterprise default cannot redirect the owner/repository to a
different host.

```bash
REPOSITORY=owner/electronic-mail
METADATA=artifacts/macos-beta/ElectronicMail-Beta-0.1.0-1-metadata.json

npm run release:macos:beta:github:create-draft -- \
  --repo "$REPOSITORY" \
  --metadata "$METADATA"
```

Review the draft in GitHub and independently verify it again without changing
remote state:

```bash
npm run release:macos:beta:github:verify-draft -- \
  --repo "$REPOSITORY" \
  --metadata "$METADATA"
```

Publishing is a separate, explicit operation. Copy the complete
`source_commit` from the generated metadata; a short SHA is rejected. Immediately
before publishing, the command re-resolves the remote tag and commit, requires
the release to still be a draft prerelease, downloads and byte-verifies all four
assets again, and only then changes `draft` to false. It performs the same
verification once more after GitHub publishes the prerelease:

```bash
SOURCE_COMMIT=0123456789abcdef0123456789abcdef01234567

npm run release:macos:beta:github:publish -- \
  --repo "$REPOSITORY" \
  --metadata "$METADATA" \
  --confirm-source-commit "$SOURCE_COMMIT"
```

The publisher never marks this beta as latest or production-ready. If any local
or remote file, tag, commit, draft state, prerelease state, filename, size, or
digest differs, it exits before the publish command.

Tell testers to use a dedicated Gmail account. For localhost builds, start the backend before opening the app. macOS may block an unnotarized download or require an explicit tester override; this beta path intentionally does not run `spctl` or claim that Gatekeeper will accept it. Library validation is disabled only in this local-testing artifact, so testers must verify the exact source SHA and DMG checksum recorded in the metadata before running it.

Beta and production intentionally share the backend-compatible `electronicmail://` OAuth callback scheme. macOS does not guarantee which app receives a shared custom-scheme callback when both are installed. Before signing in or reconnecting an account in the beta, remove every production `Electronic Mail.app` copy from the Mac; production can be reinstalled after beta authentication finishes. The beta README, release notes, and metadata repeat this limitation, and the mounted-DMG check fails if the README warning is absent.
