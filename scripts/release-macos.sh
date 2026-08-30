#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=macos-release-toolchain.env
source "$ROOT_DIR/scripts/macos-release-toolchain.env"
VALIDATE_INPUTS_ONLY=0

if [ "${1:-}" = "--preflight" ]; then
  shift
  [ "$#" -eq 0 ] || { echo "release error: --preflight accepts no additional arguments" >&2; exit 1; }
  exec bash "$ROOT_DIR/scripts/preflight-macos-release.sh"
fi
if [ "${1:-}" = "--validate-inputs" ]; then
  shift
  [ "$#" -eq 0 ] || { echo "release error: --validate-inputs accepts no additional arguments" >&2; exit 1; }
  VALIDATE_INPUTS_ONLY=1
fi
[ "$#" -eq 0 ] || { echo "release error: unknown argument: $1" >&2; exit 1; }

OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR/artifacts/macos}"
WORK_DIR="${WORK_DIR:-$ROOT_DIR/.release/macos}"
VERSION="${VERSION:-1.0.0}"
BUILD_NUMBER="${BUILD_NUMBER:-1}"
BACKEND_URL="${BACKEND_URL:-}"
CURRENT_YEAR="${CURRENT_YEAR:-$(date +%Y)}"
DEVELOPER_ID_APPLICATION="${DEVELOPER_ID_APPLICATION:-}"
APPLE_DEVELOPMENT_TEAM="${APPLE_DEVELOPMENT_TEAM:-}"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"
SKIP_NOTARIZATION="${SKIP_NOTARIZATION:-0}"

fail() {
  echo "release error: $*" >&2
  exit 1
}

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || fail "VERSION must contain two or three numeric components"
[[ "$BUILD_NUMBER" =~ ^[1-9][0-9]*$ ]] || fail "BUILD_NUMBER must be a positive integer"
[[ "$CURRENT_YEAR" =~ ^20[0-9]{2}$ ]] || fail "CURRENT_YEAR must be a four-digit year"
if [ "$VALIDATE_INPUTS_ONLY" != "1" ]; then
  [[ "$APPLE_DEVELOPMENT_TEAM" =~ ^[A-Z0-9]{10}$ ]] || fail "APPLE_DEVELOPMENT_TEAM must be the 10-character Apple team identifier"
fi
case "$SKIP_NOTARIZATION" in
  0 | 1) ;;
  *) fail "SKIP_NOTARIZATION must be 0 or 1" ;;
esac

[ -n "$BACKEND_URL" ] || fail "BACKEND_URL is required"
python3 - "$BACKEND_URL" <<'PY'
import ipaddress
import re
import sys
from urllib.parse import urlsplit

value = sys.argv[1]
if value != value.strip() or any(ord(character) < 33 for character in value):
    raise SystemExit("BACKEND_URL cannot contain whitespace or control characters")
try:
    url = urlsplit(value)
except ValueError as error:
    raise SystemExit(f"BACKEND_URL is invalid: {error}")
if url.scheme != "https" or not url.hostname:
    raise SystemExit("BACKEND_URL must be the production HTTPS API origin")
if url.username or url.password or url.query or url.fragment:
    raise SystemExit("BACKEND_URL cannot include credentials, a query, or a fragment")
if url.path:
    raise SystemExit("BACKEND_URL must be an origin without a path")
try:
    port = url.port
except ValueError as error:
    raise SystemExit(f"BACKEND_URL has an invalid port: {error}")
host = url.hostname
if not host.isascii() or host != host.lower() or host.endswith("."):
    raise SystemExit("BACKEND_URL hostname must be lowercase canonical ASCII without a trailing dot")
reserved_suffixes = (".invalid", ".test", ".example", ".localhost", ".local")
reserved_hosts = {"example.com", "example.net", "example.org"}
if host == "localhost" or host in reserved_hosts or host.endswith(reserved_suffixes) or any(host.endswith(f".{value}") for value in reserved_hosts):
    raise SystemExit("BACKEND_URL cannot use a local, reserved, or placeholder hostname")
try:
    address = ipaddress.ip_address(host)
except ValueError:
    address = None
if address is not None:
    if not address.is_global:
        raise SystemExit("BACKEND_URL cannot use a private, loopback, or link-local IP address")
    canonical_address = address.compressed
    if host != canonical_address:
        raise SystemExit("BACKEND_URL IP address must use its canonical compressed representation")
    canonical_host = f"[{canonical_address}]" if address.version == 6 else canonical_address
else:
    labels = host.split(".")
    valid_label = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
    if (
        "." not in host
        or len(host) > 253
        or re.fullmatch(r"[0-9.]+", host)
        or any(not valid_label.fullmatch(label) for label in labels)
    ):
        raise SystemExit("BACKEND_URL must use valid lowercase ASCII DNS labels or a canonical public IP address")
    canonical_host = host
if port == 443:
    raise SystemExit("BACKEND_URL must omit the default HTTPS port")
canonical_netloc = canonical_host if port is None else f"{canonical_host}:{port}"
if url.netloc != canonical_netloc or value != f"https://{canonical_netloc}":
    raise SystemExit("BACKEND_URL must be a canonical HTTPS origin")
PY

if [ "$VALIDATE_INPUTS_ONLY" = "1" ]; then
  echo "macOS production release inputs are valid: version $VERSION ($BUILD_NUMBER), backend $BACKEND_URL"
  exit 0
fi

[ -n "$DEVELOPER_ID_APPLICATION" ] || fail "DEVELOPER_ID_APPLICATION is required"
[[ "$DEVELOPER_ID_APPLICATION" == Developer\ ID\ Application:\ *" ($APPLE_DEVELOPMENT_TEAM)" ]] || \
  fail "DEVELOPER_ID_APPLICATION must be the full Developer ID Application identity for APPLE_DEVELOPMENT_TEAM"
[[ "$DEVELOPER_ID_APPLICATION" != *$'\n'* && "$DEVELOPER_ID_APPLICATION" != *$'\r'* ]] || \
  fail "DEVELOPER_ID_APPLICATION cannot contain line breaks"
if [ "$SKIP_NOTARIZATION" != "1" ]; then
  [ -n "$NOTARY_PROFILE" ] || fail "NOTARY_PROFILE is required (store credentials with xcrun notarytool store-credentials)"
  [[ "$NOTARY_PROFILE" =~ ^[A-Za-z0-9._-]+$ ]] || fail "NOTARY_PROFILE may contain only letters, numbers, dot, underscore, and hyphen"
else
  if [ "$OUTPUT_DIR" = "$ROOT_DIR/artifacts/macos" ]; then
    OUTPUT_DIR="$ROOT_DIR/artifacts/macos-local-unnotarized"
  fi
  echo "WARNING: SKIP_NOTARIZATION=1 produces a signed local artifact that is NOT FOR DISTRIBUTION." >&2
fi

for command in codesign ditto git hdiutil lipo plutil python3 security shasum spctl xcrun; do
  command -v "$command" >/dev/null 2>&1 || fail "required command not found: $command"
done

DEVELOPER_DIR="${DEVELOPER_DIR:-$APPROVED_DEVELOPER_DIR}"
EXPECTED_XCODE_VERSION="${EXPECTED_XCODE_VERSION:-$APPROVED_XCODE_VERSION}"
EXPECTED_XCODE_BUILD="${EXPECTED_XCODE_BUILD:-$APPROVED_XCODE_BUILD}"
export DEVELOPER_DIR EXPECTED_XCODE_VERSION EXPECTED_XCODE_BUILD
bash "$ROOT_DIR/scripts/verify-macos-toolchain.sh"
RELEASE_DEVELOPER_DIR="$DEVELOPER_DIR"

if [ "$SKIP_NOTARIZATION" != "1" ] && [ -n "$(git -C "$ROOT_DIR" status --porcelain --untracked-files=all)" ]; then
  fail "the production source tree must be clean and committed before signing"
fi
SOURCE_COMMIT="$(git -C "$ROOT_DIR" rev-parse HEAD)"
SOURCE_TREE_CLEAN=true
if [ -n "$(git -C "$ROOT_DIR" status --porcelain --untracked-files=all)" ]; then
  SOURCE_TREE_CLEAN=false
fi
XCODE_VERSION="$("$RELEASE_DEVELOPER_DIR/usr/bin/xcodebuild" -version | sed -n '1s/^Xcode //p')"
XCODE_BUILD="$("$RELEASE_DEVELOPER_DIR/usr/bin/xcodebuild" -version | sed -n '2s/^Build version //p')"

if ! security find-identity -v -p codesigning | grep -F -- "\"$DEVELOPER_ID_APPLICATION\"" >/dev/null; then
  fail "Developer ID signing identity is not available in the keychain"
fi

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

STALE_MOUNT_POINT="$WORK_DIR/dmg-verification-mount"
if /sbin/mount | grep -F -- " on $STALE_MOUNT_POINT (" >/dev/null 2>&1; then
  hdiutil detach "$STALE_MOUNT_POINT" >/dev/null 2>&1 || fail "could not detach stale DMG verification mount at $STALE_MOUNT_POINT"
fi
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR" "$OUTPUT_DIR"

ARCHIVE_PATH="$WORK_DIR/ElectronicMail.xcarchive"
APP_PATH="$ARCHIVE_PATH/Products/Applications/Electronic Mail.app"
DSYM_PATH="$ARCHIVE_PATH/dSYMs/Electronic Mail.app.dSYM"
APP_ZIP="$OUTPUT_DIR/ElectronicMail-$VERSION-$BUILD_NUMBER.zip"
DMG_PATH="$OUTPUT_DIR/ElectronicMail-$VERSION-$BUILD_NUMBER.dmg"
DSYM_ZIP="$OUTPUT_DIR/ElectronicMail-$VERSION-$BUILD_NUMBER-dSYMs.zip"
ARCHIVE_ZIP="$OUTPUT_DIR/ElectronicMail-$VERSION-$BUILD_NUMBER.xcarchive.zip"
CHECKSUM_PATH="$OUTPUT_DIR/SHA256SUMS-$VERSION-$BUILD_NUMBER.txt"
METADATA_PATH="$OUTPUT_DIR/RELEASE-METADATA-$VERSION-$BUILD_NUMBER.json"
NOTARY_UPLOAD_ZIP="$WORK_DIR/ElectronicMail-notary-upload.zip"
APP_NOTARY_RESULT="$OUTPUT_DIR/NOTARY-APP-$VERSION-$BUILD_NUMBER.json"
DMG_NOTARY_RESULT="$OUTPUT_DIR/NOTARY-DMG-$VERSION-$BUILD_NUMBER.json"
APP_NOTARY_LOG="$OUTPUT_DIR/NOTARY-APP-LOG-$VERSION-$BUILD_NUMBER.json"
DMG_NOTARY_LOG="$OUTPUT_DIR/NOTARY-DMG-LOG-$VERSION-$BUILD_NUMBER.json"
DMG_MOUNT_POINT="$WORK_DIR/dmg-verification-mount"
DMG_IS_MOUNTED=0

cleanup_release() {
  if [ "$DMG_IS_MOUNTED" = "1" ]; then
    hdiutil detach "$DMG_MOUNT_POINT" >/dev/null 2>&1 || true
  fi
}

trap cleanup_release EXIT

rm -f "$APP_ZIP" "$DMG_PATH" "$DSYM_ZIP" "$ARCHIVE_ZIP" "$CHECKSUM_PATH" "$METADATA_PATH" \
  "$APP_NOTARY_RESULT" "$DMG_NOTARY_RESULT" "$APP_NOTARY_LOG" "$DMG_NOTARY_LOG" \
  "$OUTPUT_DIR/NOT_FOR_DISTRIBUTION.txt"

echo "==> Archiving Electronic Mail $VERSION ($BUILD_NUMBER)"
bash "$ROOT_DIR/scripts/xcode.sh" archive \
  -project "$ROOT_DIR/macos/ElectronicMail/ElectronicMail.xcodeproj" \
  -scheme ElectronicMail \
  -configuration Release \
  -destination "generic/platform=macOS" \
  -archivePath "$ARCHIVE_PATH" \
  -jobs 2 \
  ARCHS="arm64 x86_64" \
  ONLY_ACTIVE_ARCH=NO \
  COMPILER_INDEX_STORE_ENABLE=NO \
  CODE_SIGN_STYLE=Manual \
  CODE_SIGNING_ALLOWED=YES \
  CODE_SIGNING_REQUIRED=YES \
  CODE_SIGN_INJECT_BASE_ENTITLEMENTS=YES \
  SWIFT_ACTIVE_COMPILATION_CONDITIONS= \
  "CODE_SIGN_IDENTITY=$DEVELOPER_ID_APPLICATION" \
  "DEVELOPMENT_TEAM=$APPLE_DEVELOPMENT_TEAM" \
  "ELECTRONIC_MAIL_BACKEND_URL=$BACKEND_URL" \
  "MARKETING_VERSION=$VERSION" \
  "CURRENT_PROJECT_VERSION=$BUILD_NUMBER" \
  "CURRENT_YEAR=$CURRENT_YEAR" \
  "ELECTRONIC_MAIL_SOURCE_COMMIT=$SOURCE_COMMIT" \
  "OTHER_CODE_SIGN_FLAGS=--timestamp"

[ -d "$APP_PATH" ] || fail "archive did not contain Electronic Mail.app"
[ -d "$DSYM_PATH" ] || fail "archive is missing Electronic Mail.app.dSYM"

APP_PATH="$APP_PATH" \
EXPECTED_BACKEND_URL="$BACKEND_URL" \
EXPECTED_VERSION="$VERSION" \
EXPECTED_BUILD_NUMBER="$BUILD_NUMBER" \
EXPECTED_SOURCE_COMMIT="$SOURCE_COMMIT" \
EXPECTED_TEAM_ID="$APPLE_DEVELOPMENT_TEAM" \
DSYM_PATH="$DSYM_PATH" \
SIGNING_MODE=developer-id \
INFO_POLICY=production \
REQUIRE_ADHOC_SIGNATURE=0 \
REQUIRE_NOTARIZATION=0 \
bash "$ROOT_DIR/scripts/verify-macos-release.sh"

submit_for_notarization() {
  local artifact_path="$1"
  local result_path="$2"
  local log_path="$3"
  local artifact_label="$4"
  local submission_id

  echo "==> Submitting $artifact_label for notarization"
  if ! xcrun notarytool submit "$artifact_path" \
    --keychain-profile "$NOTARY_PROFILE" \
    --wait \
    --output-format json > "$result_path"; then
    fail "$artifact_label notarization request failed; inspect $result_path"
  fi
  submission_id="$(python3 - "$result_path" "$artifact_label" <<'PY'
import json
import sys

path, label = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    result = json.load(handle)
if result.get("status") != "Accepted":
    raise SystemExit(f"{label} notarization was not accepted: {result.get('status', 'unknown')}")
if not result.get("id"):
    raise SystemExit(f"{label} notarization response is missing the submission id")
print(result["id"])
PY
)"

  xcrun notarytool log --keychain-profile "$NOTARY_PROFILE" "$submission_id" "$log_path"
  python3 - "$log_path" "$artifact_label" <<'PY'
import json
import sys

path, label = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    result = json.load(handle)
if result.get("status") != "Accepted" or result.get("statusCode") != 0:
    raise SystemExit(f"{label} notarization log is not cleanly accepted")
if result.get("issues") not in (None, []):
    raise SystemExit(f"{label} notarization log contains issues; inspect {path}")
PY
}

verify_developer_id_container_signature() {
  local artifact_path="$1"
  local signature_details
  local actual_team_id

  codesign --verify --verbose=2 "$artifact_path"
  signature_details="$(codesign -dvvv "$artifact_path" 2>&1)"
  printf '%s\n' "$signature_details" | grep -q '^Authority=Developer ID Application:' || \
    fail "container is not signed with a Developer ID Application certificate: $artifact_path"
  printf '%s\n' "$signature_details" | grep -q '^Timestamp=' || \
    fail "container secure signing timestamp is missing: $artifact_path"
  actual_team_id="$(printf '%s\n' "$signature_details" | sed -n 's/^TeamIdentifier=//p' | head -1)"
  [ "$actual_team_id" = "$APPLE_DEVELOPMENT_TEAM" ] || \
    fail "container signature team mismatch: expected $APPLE_DEVELOPMENT_TEAM, found ${actual_team_id:-none}"
}

if [ "$SKIP_NOTARIZATION" != "1" ]; then
  rm -f "$NOTARY_UPLOAD_ZIP"
  ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$NOTARY_UPLOAD_ZIP"
  submit_for_notarization "$NOTARY_UPLOAD_ZIP" "$APP_NOTARY_RESULT" "$APP_NOTARY_LOG" "the app"
  xcrun stapler staple "$APP_PATH"
  xcrun stapler validate "$APP_PATH"
fi

# Package the public ZIP only after stapling so the distributed app carries its ticket.
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$APP_ZIP"

DMG_STAGE="$WORK_DIR/dmg"
mkdir -p "$DMG_STAGE"
ditto "$APP_PATH" "$DMG_STAGE/Electronic Mail.app"
ln -s /Applications "$DMG_STAGE/Applications"
hdiutil create -volname "Electronic Mail" -srcfolder "$DMG_STAGE" -ov -format UDZO "$DMG_PATH"
hdiutil verify "$DMG_PATH"
codesign --force --sign "$DEVELOPER_ID_APPLICATION" --timestamp "$DMG_PATH"
verify_developer_id_container_signature "$DMG_PATH"

if [ "$SKIP_NOTARIZATION" != "1" ]; then
  submit_for_notarization "$DMG_PATH" "$DMG_NOTARY_RESULT" "$DMG_NOTARY_LOG" "the DMG"
  xcrun stapler staple "$DMG_PATH"
  xcrun stapler validate "$DMG_PATH"
  spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG_PATH"

  APP_PATH="$APP_PATH" \
  EXPECTED_BACKEND_URL="$BACKEND_URL" \
  EXPECTED_VERSION="$VERSION" \
  EXPECTED_BUILD_NUMBER="$BUILD_NUMBER" \
  EXPECTED_SOURCE_COMMIT="$SOURCE_COMMIT" \
  EXPECTED_TEAM_ID="$APPLE_DEVELOPMENT_TEAM" \
  SIGNING_MODE=developer-id \
  INFO_POLICY=production \
  REQUIRE_ADHOC_SIGNATURE=0 \
  REQUIRE_NOTARIZATION=1 \
  bash "$ROOT_DIR/scripts/verify-macos-release.sh"
fi

echo "==> Verifying packaged ZIP contents"
ZIP_VERIFY_DIR="$WORK_DIR/zip-verification"
rm -rf "$ZIP_VERIFY_DIR"
mkdir -p "$ZIP_VERIFY_DIR"
ditto -x -k "$APP_ZIP" "$ZIP_VERIFY_DIR"
ZIP_APP_PATH="$ZIP_VERIFY_DIR/Electronic Mail.app"
[ -d "$ZIP_APP_PATH" ] || fail "public ZIP does not contain Electronic Mail.app at its root"
ZIP_REQUIRE_NOTARIZATION=0
if [ "$SKIP_NOTARIZATION" != "1" ]; then
  ZIP_REQUIRE_NOTARIZATION=1
fi
APP_PATH="$ZIP_APP_PATH" \
EXPECTED_BACKEND_URL="$BACKEND_URL" \
EXPECTED_VERSION="$VERSION" \
EXPECTED_BUILD_NUMBER="$BUILD_NUMBER" \
EXPECTED_SOURCE_COMMIT="$SOURCE_COMMIT" \
EXPECTED_TEAM_ID="$APPLE_DEVELOPMENT_TEAM" \
SIGNING_MODE=developer-id \
INFO_POLICY=production \
REQUIRE_ADHOC_SIGNATURE=0 \
REQUIRE_NOTARIZATION="$ZIP_REQUIRE_NOTARIZATION" \
bash "$ROOT_DIR/scripts/verify-macos-release.sh"

echo "==> Verifying installed app inside the DMG"
rm -rf "$DMG_MOUNT_POINT"
mkdir -p "$DMG_MOUNT_POINT"
hdiutil attach -readonly -nobrowse -mountpoint "$DMG_MOUNT_POINT" "$DMG_PATH" >/dev/null
DMG_IS_MOUNTED=1
python3 "$ROOT_DIR/scripts/verify_macos_dmg_layout.py" \
  --mount "$DMG_MOUNT_POINT" \
  --app-name "Electronic Mail.app"
DMG_APP_PATH="$DMG_MOUNT_POINT/Electronic Mail.app"
APP_PATH="$DMG_APP_PATH" \
EXPECTED_BACKEND_URL="$BACKEND_URL" \
EXPECTED_VERSION="$VERSION" \
EXPECTED_BUILD_NUMBER="$BUILD_NUMBER" \
EXPECTED_SOURCE_COMMIT="$SOURCE_COMMIT" \
EXPECTED_TEAM_ID="$APPLE_DEVELOPMENT_TEAM" \
SIGNING_MODE=developer-id \
INFO_POLICY=production \
REQUIRE_ADHOC_SIGNATURE=0 \
REQUIRE_NOTARIZATION="$ZIP_REQUIRE_NOTARIZATION" \
bash "$ROOT_DIR/scripts/verify-macos-release.sh"
hdiutil detach "$DMG_MOUNT_POINT" >/dev/null
DMG_IS_MOUNTED=0

ditto -c -k --sequesterRsrc --keepParent "$ARCHIVE_PATH/dSYMs" "$DSYM_ZIP"
ditto -c -k --sequesterRsrc --keepParent "$ARCHIVE_PATH" "$ARCHIVE_ZIP"

python3 - "$METADATA_PATH" "$VERSION" "$BUILD_NUMBER" "$BACKEND_URL" "$APPLE_DEVELOPMENT_TEAM" "$SKIP_NOTARIZATION" "$CURRENT_YEAR" "$SOURCE_COMMIT" "$SOURCE_TREE_CLEAN" "$XCODE_VERSION" "$XCODE_BUILD" <<'PY'
import json
import sys
from datetime import datetime, timezone

path, version, build, backend, team, skipped, year, source_commit, source_tree_clean, xcode_version, xcode_build = sys.argv[1:]
metadata = {
    "product": "Electronic Mail",
    "bundle_identifier": "app.electronicmail.mac",
    "version": version,
    "build_number": build,
    "backend_origin": backend,
    "developer_team": team,
    "architectures": ["arm64", "x86_64"],
    "minimum_macos": "14.0",
    "notarized": skipped != "1",
    "copyright_year": year,
    "source_commit": source_commit,
    "source_tree_clean": source_tree_clean == "true",
    "xcode_version": xcode_version,
    "xcode_build": xcode_build,
    "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(metadata, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY

(
  cd "$OUTPUT_DIR"
  shasum -a 256 "$(basename "$APP_ZIP")" "$(basename "$DMG_PATH")" \
    "$(basename "$DSYM_ZIP")" "$(basename "$ARCHIVE_ZIP")" \
    "$(basename "$METADATA_PATH")" > "$(basename "$CHECKSUM_PATH")"
  if [ "$SKIP_NOTARIZATION" != "1" ]; then
    shasum -a 256 \
      "$(basename "$APP_NOTARY_RESULT")" "$(basename "$DMG_NOTARY_RESULT")" \
      "$(basename "$APP_NOTARY_LOG")" "$(basename "$DMG_NOTARY_LOG")" >> "$(basename "$CHECKSUM_PATH")"
  fi
  shasum -a 256 -c "$(basename "$CHECKSUM_PATH")"
)

if [ "$SKIP_NOTARIZATION" = "1" ]; then
  printf '%s\n' "NOT FOR DISTRIBUTION: this artifact is signed but was not notarized or accepted by Gatekeeper." > "$OUTPUT_DIR/NOT_FOR_DISTRIBUTION.txt"
  echo "==> NOT FOR DISTRIBUTION: notarization, stapling, and Gatekeeper acceptance were skipped" >&2
fi

echo "==> Release artifacts are in $OUTPUT_DIR"
