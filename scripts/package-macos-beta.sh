#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VALIDATE_INPUTS_ONLY=0

if [ "${1:-}" = "--validate-inputs" ]; then
  shift
  [ "$#" -eq 0 ] || { echo "beta package error: --validate-inputs accepts no additional arguments" >&2; exit 1; }
  VALIDATE_INPUTS_ONLY=1
fi
[ "$#" -eq 0 ] || { echo "beta package error: unknown argument: $1" >&2; exit 1; }

OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR/artifacts/macos-beta}"
WORK_DIR="${WORK_DIR:-$ROOT_DIR/.release/macos-beta}"
VERSION="${VERSION:-0.1.0}"
BUILD_NUMBER="${BUILD_NUMBER:-1}"
BACKEND_URL="${BACKEND_URL:-http://localhost:3001}"
CURRENT_YEAR="${CURRENT_YEAR:-$(date +%Y)}"

fail() {
  echo "beta package error: $*" >&2
  exit 1
}

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || fail "VERSION must contain two or three numeric components"
[[ "$BUILD_NUMBER" =~ ^[1-9][0-9]*$ ]] || fail "BUILD_NUMBER must be a positive integer"
[[ "$CURRENT_YEAR" =~ ^20[0-9]{2}$ ]] || fail "CURRENT_YEAR must be a four-digit year"

python3 - "$BACKEND_URL" <<'PY'
import ipaddress
import sys
from urllib.parse import urlsplit

value = sys.argv[1]
if value != value.strip() or any(ord(character) < 33 for character in value):
    raise SystemExit("BACKEND_URL cannot contain whitespace or control characters")
if value == "http://localhost:3001":
    raise SystemExit(0)

url = urlsplit(value)
if url.scheme != "https" or not url.hostname:
    raise SystemExit("BACKEND_URL must be exactly http://localhost:3001 or a canonical public HTTPS origin")
if url.username or url.password or url.query or url.fragment or url.path:
    raise SystemExit("public BACKEND_URL cannot include credentials, a path, a query, or a fragment")
try:
    port = url.port
except ValueError as error:
    raise SystemExit(f"BACKEND_URL has an invalid port: {error}")

host = url.hostname
if host != host.lower() or host.endswith("."):
    raise SystemExit("public BACKEND_URL hostname must be lowercase and must not end in a dot")
try:
    address = ipaddress.ip_address(host)
except ValueError:
    address = None

reserved_suffixes = (".invalid", ".test", ".example", ".localhost", ".local")
reserved_hosts = {"example.com", "example.net", "example.org", "localhost"}
if host in reserved_hosts or host.endswith(reserved_suffixes) or any(host.endswith(f".{item}") for item in reserved_hosts):
    raise SystemExit("public BACKEND_URL cannot use a local, reserved, or placeholder hostname")
if address is not None:
    if not address.is_global:
        raise SystemExit("public BACKEND_URL cannot use a private, loopback, or link-local IP address")
    canonical_host = f"[{host}]" if address.version == 6 else host
else:
    if "." not in host:
        raise SystemExit("public BACKEND_URL must use a fully qualified public hostname")
    try:
        canonical_host = host.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise SystemExit(f"BACKEND_URL hostname is invalid: {error}")
    if canonical_host != host:
        raise SystemExit("public BACKEND_URL must use its lowercase ASCII canonical hostname")

if port == 443:
    raise SystemExit("public BACKEND_URL must omit the default HTTPS port")
canonical_netloc = canonical_host if port is None else f"{canonical_host}:{port}"
if url.netloc != canonical_netloc or value != f"https://{canonical_netloc}":
    raise SystemExit("public BACKEND_URL must be a canonical HTTPS origin")
PY

if [ "$VALIDATE_INPUTS_ONLY" = "1" ]; then
  echo "macOS local beta inputs are valid: version $VERSION ($BUILD_NUMBER), backend $BACKEND_URL"
  exit 0
fi

for command in codesign ditto git hdiutil lipo plutil python3 shasum tar; do
  command -v "$command" >/dev/null 2>&1 || fail "required command not found: $command"
done

if [ -n "$(git -C "$ROOT_DIR" status --porcelain --untracked-files=all)" ]; then
  fail "the local beta source tree must be clean and committed before packaging"
fi
SOURCE_COMMIT="$(git -C "$ROOT_DIR" rev-parse HEAD)"
[[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]] || fail "Git did not return a full lowercase source commit SHA"

if [ -n "${DEVELOPER_DIR:-}" ] && [ -x "$DEVELOPER_DIR/usr/bin/xcodebuild" ]; then
  BETA_DEVELOPER_DIR="$DEVELOPER_DIR"
elif [ -x /Applications/Xcode.app/Contents/Developer/usr/bin/xcodebuild ]; then
  BETA_DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
elif [ -x /Applications/Xcode-beta.app/Contents/Developer/usr/bin/xcodebuild ]; then
  BETA_DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer
else
  fail "full Xcode is required; set DEVELOPER_DIR to its Contents/Developer directory"
fi
XCODEBUILD="$BETA_DEVELOPER_DIR/usr/bin/xcodebuild"
XCODE_VERSION="$($XCODEBUILD -version | sed -n '1s/^Xcode //p')"
XCODE_BUILD="$($XCODEBUILD -version | sed -n '2s/^Build version //p')"
[ -n "$XCODE_VERSION" ] && [ -n "$XCODE_BUILD" ] || fail "could not identify the selected Xcode version and build"

WORK_DIR="$(python3 - "$WORK_DIR" /tmp "$ROOT_DIR/.release" "${RUNNER_TEMP:-}" <<'PY'
import os
import sys

candidate = os.path.realpath(sys.argv[1])
allowed_roots = [os.path.realpath(value) for value in sys.argv[2:] if value]
for root in allowed_roots:
    try:
        inside = os.path.commonpath((candidate, root)) == root
    except ValueError:
        inside = False
    if inside and candidate != root:
        print(candidate)
        break
else:
    raise SystemExit("WORK_DIR must resolve below /tmp, the repository .release directory, or RUNNER_TEMP")
PY
)"

DMG_MOUNT_POINT="$WORK_DIR/dmg-verification-mount"
DMG_IS_MOUNTED=0
cleanup_beta_package() {
  if [ "$DMG_IS_MOUNTED" = "1" ]; then
    hdiutil detach "$DMG_MOUNT_POINT" >/dev/null 2>&1 || true
  fi
}
trap cleanup_beta_package EXIT

if /sbin/mount | grep -F -- " on $DMG_MOUNT_POINT (" >/dev/null 2>&1; then
  hdiutil detach "$DMG_MOUNT_POINT" >/dev/null 2>&1 || fail "could not detach stale beta DMG verification mount"
fi
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR" "$OUTPUT_DIR"

SOURCE_SNAPSHOT_ROOT="$WORK_DIR/source-snapshot"
SOURCE_COPY="$SOURCE_SNAPSHOT_ROOT/macos/ElectronicMail"
DERIVED_DATA_PATH="$WORK_DIR/DerivedData"
APP_PATH="$DERIVED_DATA_PATH/Build/Products/Release/ElectronicMail.app"
DSYM_PATH="$DERIVED_DATA_PATH/Build/Products/Release/ElectronicMail.app.dSYM"
FRAMEWORK_PATH="$APP_PATH/Contents/Frameworks/ElectronicMailCore.framework"
BETA_ENTITLEMENTS_PATH="$SOURCE_COPY/Config/Entitlements/ElectronicMail-Beta.entitlements"
DMG_ROOT="$WORK_DIR/dmg-root"
DMG_APP_PATH="$DMG_ROOT/ElectronicMail.app"
BETA_README_PATH="$DMG_ROOT/README-BETA.txt"

ARTIFACT_STEM="ElectronicMail-Beta-$VERSION-$BUILD_NUMBER"
DMG_NAME="$ARTIFACT_STEM.dmg"
METADATA_NAME="$ARTIFACT_STEM-metadata.json"
RELEASE_NOTES_NAME="$ARTIFACT_STEM-release-notes.md"
CHECKSUM_NAME="$ARTIFACT_STEM-SHA256SUMS.txt"
DMG_PATH="$OUTPUT_DIR/$DMG_NAME"
METADATA_PATH="$OUTPUT_DIR/$METADATA_NAME"
RELEASE_NOTES_PATH="$OUTPUT_DIR/$RELEASE_NOTES_NAME"
CHECKSUM_PATH="$OUTPUT_DIR/$CHECKSUM_NAME"

rm -f "$DMG_PATH" "$METADATA_PATH" "$RELEASE_NOTES_PATH" "$CHECKSUM_PATH"

echo "==> Exporting the exact committed source for an isolated beta Info.plist build"
mkdir -p "$SOURCE_SNAPSHOT_ROOT"
git -C "$ROOT_DIR" archive --format=tar "$SOURCE_COMMIT" -- macos/ElectronicMail | tar -x -C "$SOURCE_SNAPSHOT_ROOT"
python3 - "$SOURCE_COPY/ElectronicMail.xcodeproj/project.pbxproj" <<'PY'
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    project = handle.read()
release = 'INFOPLIST_FILE = "Config/InfoPlists/ElectronicMail-Release-Info.plist";'
beta = 'INFOPLIST_FILE = "Config/InfoPlists/ElectronicMail-Beta-Info.plist";'
if project.count(release) != 1:
    raise SystemExit("copied Xcode project did not contain exactly one macOS app Release Info.plist setting")
if beta in project:
    raise SystemExit("copied Xcode project unexpectedly already contains the beta Info.plist setting")
with open(path, "w", encoding="utf-8", newline="") as handle:
    handle.write(project.replace(release, beta, 1))
PY

echo "==> Building Electronic Mail local beta $VERSION ($BUILD_NUMBER) from $SOURCE_COMMIT"
"$XCODEBUILD" \
  -project "$SOURCE_COPY/ElectronicMail.xcodeproj" \
  -scheme ElectronicMail \
  -configuration Release \
  -destination "generic/platform=macOS" \
  -derivedDataPath "$DERIVED_DATA_PATH" \
  -jobs 2 \
  clean build \
  ARCHS="arm64 x86_64" \
  ONLY_ACTIVE_ARCH=NO \
  COMPILER_INDEX_STORE_ENABLE=NO \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGNING_REQUIRED=NO \
  ENABLE_PREVIEWS=NO \
  'SWIFT_ACTIVE_COMPILATION_CONDITIONS=$(inherited) ELECTRONIC_MAIL_LOCAL_BETA' \
  "ELECTRONIC_MAIL_BACKEND_URL=$BACKEND_URL" \
  "MARKETING_VERSION=$VERSION" \
  "CURRENT_PROJECT_VERSION=$BUILD_NUMBER" \
  "CURRENT_YEAR=$CURRENT_YEAR" \
  "ELECTRONIC_MAIL_SOURCE_COMMIT=$SOURCE_COMMIT"

[ -d "$APP_PATH" ] || fail "Release build did not produce ElectronicMail.app"
[ -d "$DSYM_PATH" ] || fail "Release build did not produce ElectronicMail.app.dSYM"
[ -d "$FRAMEWORK_PATH" ] || fail "Release build did not embed ElectronicMailCore.framework"

echo "==> Applying explicit identity-free ad-hoc signatures"
codesign --force --sign - --options runtime "$FRAMEWORK_PATH"
codesign --force --sign - --options runtime --entitlements "$BETA_ENTITLEMENTS_PATH" "$APP_PATH"

APP_PATH="$APP_PATH" \
EXPECTED_BACKEND_URL="$BACKEND_URL" \
EXPECTED_VERSION="$VERSION" \
EXPECTED_BUILD_NUMBER="$BUILD_NUMBER" \
EXPECTED_SOURCE_COMMIT="$SOURCE_COMMIT" \
DSYM_PATH="$DSYM_PATH" \
SIGNING_MODE=identity-free \
INFO_POLICY=local-beta \
REQUIRE_ADHOC_SIGNATURE=1 \
REQUIRE_NOTARIZATION=0 \
bash "$ROOT_DIR/scripts/verify-macos-release.sh"

echo "==> Smoke-testing the signed app's dynamic-library launch policy"
if ! "$APP_PATH/Contents/MacOS/ElectronicMail" --electronic-mail-beta-launch-smoke; then
  fail "signed beta app failed its headless launch smoke"
fi

mkdir -p "$DMG_ROOT"
ditto "$APP_PATH" "$DMG_APP_PATH"
ln -s /Applications "$DMG_ROOT/Applications"
python3 - "$BETA_README_PATH" "$VERSION" "$BUILD_NUMBER" "$SOURCE_COMMIT" "$BACKEND_URL" <<'PY'
import sys

path, version, build, source, backend = sys.argv[1:]
text = f"""ELECTRONIC MAIL — LOCAL TESTING BETA
====================================

UNNOTARIZED TEST SOFTWARE — NOT A PRODUCTION RELEASE

This app is ad-hoc signed, has not been notarized by Apple, and makes no
Gatekeeper-acceptance claim. Install and run it only if you trust the exact
Git source commit shown below. Do not redistribute it as a production build.

Version: {version}
Build: {build}
Source commit: {source}
Backend origin: {backend}
Configuration: optimized Release, local-beta Keychain policy
Architectures: arm64 and x86_64
Hardened Runtime: enabled
Library validation: disabled only for this identity-free local beta

LIBRARY VALIDATION IS DISABLED IN THIS TEST BUILD. This narrow runtime
exception lets the ad-hoc-signed app load its ad-hoc-signed embedded framework.
The signed and notarized production release does not contain this exception.

To install, drag ElectronicMail.app to the Applications shortcut. The app is
named “Electronic Mail Beta” in Finder and in the menu bar. Because it is not
notarized, macOS may refuse to open it without an explicit tester override.

For local-backend builds, start the backend on http://localhost:3001 before
signing in. Test data and credentials are real: use a dedicated test account.
"""
with open(path, "w", encoding="utf-8", newline="\n") as handle:
    handle.write(text)
PY

echo "==> Creating compressed local-testing DMG"
hdiutil create \
  -volname "Electronic Mail Beta" \
  -srcfolder "$DMG_ROOT" \
  -fs HFS+ \
  -format UDZO \
  -imagekey zlib-level=9 \
  -ov \
  "$DMG_PATH" >/dev/null
hdiutil verify "$DMG_PATH" >/dev/null

mkdir -p "$DMG_MOUNT_POINT"
hdiutil attach "$DMG_PATH" -nobrowse -readonly -mountpoint "$DMG_MOUNT_POINT" >/dev/null
DMG_IS_MOUNTED=1

python3 - "$DMG_MOUNT_POINT" <<'PY'
import os
import sys

root = sys.argv[1]
expected = {"Applications", "ElectronicMail.app", "README-BETA.txt"}
actual = set(os.listdir(root))
if actual != expected:
    raise SystemExit(f"mounted beta DMG has unexpected root contents: {sorted(actual)}")
applications = os.path.join(root, "Applications")
if not os.path.islink(applications) or os.readlink(applications) != "/Applications":
    raise SystemExit("mounted beta DMG Applications item is not the exact /Applications symlink")
readme = os.path.join(root, "README-BETA.txt")
with open(readme, encoding="utf-8") as handle:
    text = handle.read()
for marker in ("UNNOTARIZED TEST SOFTWARE", "NOT A PRODUCTION RELEASE", "LIBRARY VALIDATION IS DISABLED", "Gatekeeper-acceptance claim"):
    if marker not in text:
        raise SystemExit(f"mounted beta README is missing warning: {marker}")
PY

APP_PATH="$DMG_MOUNT_POINT/ElectronicMail.app" \
EXPECTED_BACKEND_URL="$BACKEND_URL" \
EXPECTED_VERSION="$VERSION" \
EXPECTED_BUILD_NUMBER="$BUILD_NUMBER" \
EXPECTED_SOURCE_COMMIT="$SOURCE_COMMIT" \
SIGNING_MODE=identity-free \
INFO_POLICY=local-beta \
REQUIRE_ADHOC_SIGNATURE=1 \
REQUIRE_NOTARIZATION=0 \
bash "$ROOT_DIR/scripts/verify-macos-release.sh"

echo "==> Smoke-testing the mounted DMG app's dynamic-library launch policy"
if ! "$DMG_MOUNT_POINT/ElectronicMail.app/Contents/MacOS/ElectronicMail" --electronic-mail-beta-launch-smoke; then
  fail "mounted beta DMG app failed its headless launch smoke"
fi

hdiutil detach "$DMG_MOUNT_POINT" >/dev/null
DMG_IS_MOUNTED=0

DMG_SHA256="$(shasum -a 256 "$DMG_PATH" | awk '{print $1}')"
[[ "$DMG_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "could not calculate the beta DMG SHA-256"
CREATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

python3 - "$METADATA_PATH" "$VERSION" "$BUILD_NUMBER" "$SOURCE_COMMIT" "$BACKEND_URL" "$DMG_NAME" "$DMG_SHA256" "$CREATED_AT" "$XCODE_VERSION" "$XCODE_BUILD" <<'PY'
import json
import os
import sys

(
    path,
    version,
    build,
    source_commit,
    backend_url,
    dmg_name,
    dmg_sha256,
    created_at,
    xcode_version,
    xcode_build,
) = sys.argv[1:]
metadata = {
    "schema_version": 1,
    "artifact_kind": "macos-local-testing-beta-dmg",
    "distribution_channel": "github-prerelease-testing",
    "local_testing_only": True,
    "production_release": False,
    "gatekeeper_acceptance_claimed": False,
    "version": version,
    "build_number": build,
    "source_commit": source_commit,
    "source_tree_clean": True,
    "backend_url": backend_url,
    "created_at": created_at,
    "configuration": "Release",
    "architectures": ["arm64", "x86_64"],
    "minimum_macos": "14.0",
    "bundle_identifier": "app.electronicmail.mac",
    "signing": {
        "mode": "ad-hoc",
        "identity": "-",
        "developer_id": False,
        "hardened_runtime": True,
        "library_validation": False,
        "runtime_exception_scope": "local-testing-beta-only",
    },
    "notarization": {
        "performed": False,
        "stapled": False,
    },
    "privacy_manifest_verified": True,
    "xcode": {
        "version": xcode_version,
        "build": xcode_build,
    },
    "dmg": {
        "filename": dmg_name,
        "sha256": dmg_sha256,
        "size_bytes": os.path.getsize(os.path.join(os.path.dirname(path), dmg_name)),
        "format": "UDZO",
    },
}
with open(path, "w", encoding="utf-8", newline="\n") as handle:
    json.dump(metadata, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY

python3 - "$RELEASE_NOTES_PATH" "$VERSION" "$BUILD_NUMBER" "$SOURCE_COMMIT" "$BACKEND_URL" "$DMG_NAME" "$DMG_SHA256" "$METADATA_NAME" "$CHECKSUM_NAME" <<'PY'
import sys

path, version, build, source, backend, dmg, digest, metadata, checksums = sys.argv[1:]
text = f"""# Electronic Mail {version} ({build}) — local testing beta

> **Unnotarized test build.** This is an ad-hoc-signed local-testing artifact,
> not a production release. No Developer ID, notarization, stapling, or
> Gatekeeper-acceptance claim is made.
>
> Hardened Runtime remains enabled, but library validation is disabled only in
> this beta so its identity-free app can load its ad-hoc embedded framework.
> The production release forbids this runtime exception.

- DMG: `{dmg}`
- SHA-256: `{digest}`
- Source commit: `{source}`
- Backend origin: `{backend}`
- Build: optimized universal Release (`arm64` + `x86_64`)
- Metadata: `{metadata}`
- Checksum manifest: `{checksums}`

Use a dedicated test account. For the localhost build, start the backend at
`http://localhost:3001` before signing in. Drag `ElectronicMail.app` to the
Applications shortcut in the DMG. macOS may require an explicit tester
override because this beta has not been notarized.

Known distribution limitation: this artifact is intended only for trusted
GitHub prerelease testers and must not replace the signed/notarized production
release pipeline. It allows libraries signed outside an Apple Developer team,
so run it only if the source commit and checksum match this release.
"""
with open(path, "w", encoding="utf-8", newline="\n") as handle:
    handle.write(text)
PY

(
  cd "$OUTPUT_DIR"
  shasum -a 256 "$DMG_NAME" "$METADATA_NAME" "$RELEASE_NOTES_NAME"
) > "$CHECKSUM_PATH"
(
  cd "$OUTPUT_DIR"
  shasum -a 256 -c "$CHECKSUM_NAME" >/dev/null
)

echo "==> Local-testing beta DMG verified"
echo "DMG: $DMG_PATH"
echo "SHA-256: $DMG_SHA256"
echo "Metadata: $METADATA_PATH"
echo "GitHub prerelease notes: $RELEASE_NOTES_PATH"
echo "Checksums: $CHECKSUM_PATH"
echo "WARNING: ad-hoc signed and unnotarized; no Gatekeeper acceptance is claimed."
