#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=macos-release-toolchain.env
source "$ROOT_DIR/scripts/macos-release-toolchain.env"
PREFLIGHT_WORK_DIR="${PREFLIGHT_WORK_DIR:-/tmp/ElectronicMailReleasePreflight}"
VERSION="${VERSION:-0.0.0}"
BUILD_NUMBER="${BUILD_NUMBER:-1}"
BACKEND_URL="${BACKEND_URL:-https://api.preflight.electronicmail.test}"
CURRENT_YEAR="${CURRENT_YEAR:-$(date +%Y)}"
SOURCE_COMMIT=local

fail() {
  echo "macOS release preflight failed: $*" >&2
  exit 1
}

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || fail "VERSION must contain two or three numeric components"
[[ "$BUILD_NUMBER" =~ ^[1-9][0-9]*$ ]] || fail "BUILD_NUMBER must be a positive integer"
[[ "$CURRENT_YEAR" =~ ^20[0-9]{2}$ ]] || fail "CURRENT_YEAR must be a four-digit year"

for command in date lipo plutil python3; do
  command -v "$command" >/dev/null 2>&1 || fail "required command not found: $command"
done

DEVELOPER_DIR="${DEVELOPER_DIR:-$APPROVED_DEVELOPER_DIR}"
EXPECTED_XCODE_VERSION="${EXPECTED_XCODE_VERSION:-$APPROVED_XCODE_VERSION}"
EXPECTED_XCODE_BUILD="${EXPECTED_XCODE_BUILD:-$APPROVED_XCODE_BUILD}"
export DEVELOPER_DIR EXPECTED_XCODE_VERSION EXPECTED_XCODE_BUILD
bash "$ROOT_DIR/scripts/verify-macos-toolchain.sh"

python3 - "$BACKEND_URL" <<'PY'
import sys
from urllib.parse import urlsplit

value = sys.argv[1]
if value != value.strip() or any(ord(character) < 33 for character in value):
    raise SystemExit("BACKEND_URL cannot contain whitespace or control characters")
url = urlsplit(value)
if url.scheme != "https" or not url.hostname:
    raise SystemExit("BACKEND_URL must be an HTTPS origin")
if url.username or url.password or url.query or url.fragment:
    raise SystemExit("BACKEND_URL cannot include credentials, a query, or a fragment")
if url.path not in ("", "/"):
    raise SystemExit("BACKEND_URL must be an origin without a path")
try:
    url.port
except ValueError as error:
    raise SystemExit(f"BACKEND_URL has an invalid port: {error}")
PY
BACKEND_URL="${BACKEND_URL%/}"

PREFLIGHT_WORK_DIR="$(python3 - "$PREFLIGHT_WORK_DIR" /tmp "$ROOT_DIR/.release" <<'PY'
import os
import sys

candidate = os.path.realpath(sys.argv[1])
allowed_roots = [os.path.realpath(value) for value in sys.argv[2:]]
for root in allowed_roots:
    try:
        inside = os.path.commonpath((candidate, root)) == root
    except ValueError:
        inside = False
    if inside and candidate != root:
        print(candidate)
        break
else:
    raise SystemExit("PREFLIGHT_WORK_DIR must resolve below /tmp or the repository .release directory")
PY
)"

rm -rf "$PREFLIGHT_WORK_DIR"
mkdir -p "$PREFLIGHT_WORK_DIR"

DERIVED_DATA_PATH="$PREFLIGHT_WORK_DIR/DerivedData"
APP_PATH="$DERIVED_DATA_PATH/Build/Products/Release/Electronic Mail.app"
DSYM_PATH="$DERIVED_DATA_PATH/Build/Products/Release/Electronic Mail.app.dSYM"

echo "==> Building an identity-free, universal Release app (credential-free preflight)"
bash "$ROOT_DIR/scripts/xcode.sh" \
  -project "$ROOT_DIR/macos/ElectronicMail/ElectronicMail.xcodeproj" \
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
  SWIFT_ACTIVE_COMPILATION_CONDITIONS= \
  "ELECTRONIC_MAIL_BACKEND_URL=$BACKEND_URL" \
  "MARKETING_VERSION=$VERSION" \
  "CURRENT_PROJECT_VERSION=$BUILD_NUMBER" \
  "CURRENT_YEAR=$CURRENT_YEAR" \
  "ELECTRONIC_MAIL_SOURCE_COMMIT=$SOURCE_COMMIT"

[ -d "$APP_PATH" ] || fail "Release build did not produce Electronic Mail.app"

APP_PATH="$APP_PATH" \
EXPECTED_BACKEND_URL="$BACKEND_URL" \
EXPECTED_VERSION="$VERSION" \
EXPECTED_BUILD_NUMBER="$BUILD_NUMBER" \
EXPECTED_SOURCE_COMMIT="$SOURCE_COMMIT" \
DSYM_PATH="$DSYM_PATH" \
SIGNING_MODE=identity-free \
INFO_POLICY=production \
REQUIRE_ADHOC_SIGNATURE=0 \
REQUIRE_NOTARIZATION=0 \
bash "$ROOT_DIR/scripts/verify-macos-release.sh"

printf '%s\n' "NOT FOR DISTRIBUTION: this preflight app has no Developer ID identity and was not notarized." > "$PREFLIGHT_WORK_DIR/NOT_FOR_DISTRIBUTION.txt"
echo "==> Credential-free macOS release preflight passed"
echo "==> Inspection app: $APP_PATH"
