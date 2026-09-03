#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VALIDATE_INPUTS_ONLY=0

if [ "${1:-}" = "--validate-inputs" ]; then
  shift
  [ "$#" -eq 0 ] || { echo "local signed install error: --validate-inputs accepts no additional arguments" >&2; exit 1; }
  VALIDATE_INPUTS_ONLY=1
fi
[ "$#" -eq 0 ] || { echo "local signed install error: unknown argument: $1" >&2; exit 1; }

VERSION="${VERSION:-1.0.0}"
BUILD_NUMBER="${BUILD_NUMBER:-1}"
BACKEND_URL="${BACKEND_URL:-http://localhost:3001}"
CURRENT_YEAR="${CURRENT_YEAR:-$(date +%Y)}"
APPLE_DEVELOPMENT_TEAM="${APPLE_DEVELOPMENT_TEAM:-}"
DEVELOPER_ID_APPLICATION="${DEVELOPER_ID_APPLICATION:-}"
INSTALL_PATH="/Applications/Electronic Mail.app"

fail() {
  echo "local signed install error: $*" >&2
  exit 1
}

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || fail "VERSION must contain two or three numeric components"
[[ "$BUILD_NUMBER" =~ ^[1-9][0-9]*$ ]] || fail "BUILD_NUMBER must be a positive integer"
[[ "$CURRENT_YEAR" =~ ^20[0-9]{2}$ ]] || fail "CURRENT_YEAR must be a four-digit year"
[[ "$APPLE_DEVELOPMENT_TEAM" =~ ^[A-Z0-9]{10}$ ]] || fail "APPLE_DEVELOPMENT_TEAM must be the 10-character Apple team identifier"
[ -n "$DEVELOPER_ID_APPLICATION" ] || fail "DEVELOPER_ID_APPLICATION is required"
[[ "$DEVELOPER_ID_APPLICATION" == Developer\ ID\ Application:\ *" ($APPLE_DEVELOPMENT_TEAM)" ]] || \
  fail "DEVELOPER_ID_APPLICATION must be the full Developer ID Application identity for APPLE_DEVELOPMENT_TEAM"
[[ "$DEVELOPER_ID_APPLICATION" != *$'\n'* && "$DEVELOPER_ID_APPLICATION" != *$'\r'* ]] || \
  fail "DEVELOPER_ID_APPLICATION cannot contain line breaks"
[ "$BACKEND_URL" = "http://localhost:3001" ] || fail "BACKEND_URL must be exactly http://localhost:3001 for the local signed install"

if [ "$VALIDATE_INPUTS_ONLY" = "1" ]; then
  echo "macOS local Developer ID inputs are valid: version $VERSION ($BUILD_NUMBER), team $APPLE_DEVELOPMENT_TEAM"
  exit 0
fi

for command in codesign curl ditto git lipo osascript pgrep plutil python3 security; do
  command -v "$command" >/dev/null 2>&1 || fail "required command not found: $command"
done

if [ -n "${DEVELOPER_DIR:-}" ] && [ -x "$DEVELOPER_DIR/usr/bin/xcodebuild" ]; then
  LOCAL_DEVELOPER_DIR="$DEVELOPER_DIR"
elif [ -x /Applications/Xcode.app/Contents/Developer/usr/bin/xcodebuild ]; then
  LOCAL_DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
elif [ -x /Applications/Xcode-beta.app/Contents/Developer/usr/bin/xcodebuild ]; then
  LOCAL_DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer
else
  fail "full Xcode is required; set DEVELOPER_DIR to its Contents/Developer directory"
fi
XCODEBUILD="$LOCAL_DEVELOPER_DIR/usr/bin/xcodebuild"

if ! security find-identity -v -p codesigning | grep -F -- "\"$DEVELOPER_ID_APPLICATION\"" >/dev/null; then
  fail "Developer ID signing identity is not available in the login Keychain; the current installed app was not changed"
fi

READY_RESPONSE="$(curl --fail --silent --show-error --max-time 5 "$BACKEND_URL/ready")" || \
  fail "the local backend is not ready at $BACKEND_URL; the current installed app was not changed"
python3 - "$READY_RESPONSE" <<'PY'
import json
import sys

try:
    payload = json.loads(sys.argv[1])
except json.JSONDecodeError as error:
    raise SystemExit(f"local backend readiness response is not JSON: {error}")
if payload.get("status") != "ready":
    raise SystemExit(f"local backend is not ready: {payload.get('status', 'unknown')}")
PY

WORK_DIR="$(mktemp -d /tmp/electronic-mail-local-signed.XXXXXX)"
SOURCE_ROOT="$WORK_DIR/source"
SOURCE_COPY="$SOURCE_ROOT/macos/ElectronicMail"
ARCHIVE_PATH="$WORK_DIR/ElectronicMail.xcarchive"
APP_PATH="$ARCHIVE_PATH/Products/Applications/Electronic Mail.app"
DSYM_PATH="$ARCHIVE_PATH/dSYMs/Electronic Mail.app.dSYM"
STAGED_INSTALL_PATH="$WORK_DIR/Electronic Mail.app"
BACKUP_PATH="$WORK_DIR/previous-Electronic Mail.app"
FAILED_INSTALL_PATH="$WORK_DIR/failed-Electronic Mail.app"
INSTALL_REPLACED=0
INSTALL_COMPLETE=0
HAD_EXISTING=0

cleanup_local_install() {
  local status="$?"
  if [ "$INSTALL_REPLACED" = "1" ] && [ "$INSTALL_COMPLETE" != "1" ]; then
    if [ -d "$INSTALL_PATH" ]; then
      mv "$INSTALL_PATH" "$FAILED_INSTALL_PATH" || true
    fi
    if [ "$HAD_EXISTING" = "1" ] && [ -d "$BACKUP_PATH" ]; then
      mv "$BACKUP_PATH" "$INSTALL_PATH" || true
    fi
    echo "local signed install error: installation failed; the previous app was restored" >&2
  fi
  rm -rf "$WORK_DIR"
  exit "$status"
}
trap cleanup_local_install EXIT

mkdir -p "$SOURCE_ROOT"
ditto "$ROOT_DIR/macos/ElectronicMail" "$SOURCE_COPY"

python3 - "$SOURCE_COPY/ElectronicMail.xcodeproj/project.pbxproj" <<'PY'
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    project = handle.read()
release = 'INFOPLIST_FILE = "Config/InfoPlists/ElectronicMail-Release-Info.plist";'
local_signed = 'INFOPLIST_FILE = "Config/InfoPlists/ElectronicMail-LocalSigned-Info.plist";'
index = project.find(release)
if index < 0 or project.find(release, index + 1) >= 0:
    raise SystemExit("copied Xcode project did not contain exactly one macOS Release Info.plist setting")
settings_start = project.rfind("buildSettings = {", 0, index)
settings_end = project.find("\n\t\t\t};", index)
if settings_start < 0 or settings_end < 0:
    raise SystemExit("copied Xcode project macOS Release settings block is malformed")
settings_end += len("\n\t\t\t};")
settings = project[settings_start:settings_end]
if settings.count(release) != 1 or local_signed in settings:
    raise SystemExit("copied Xcode project Release Info.plist setting is not uniquely patchable")
settings = settings.replace(release, local_signed, 1)
with open(path, "w", encoding="utf-8", newline="") as handle:
    handle.write(project[:settings_start] + settings + project[settings_end:])
PY

echo "==> Building Electronic Mail $VERSION ($BUILD_NUMBER) with stable Developer ID identity"
DEVELOPER_DIR="$LOCAL_DEVELOPER_DIR" "$XCODEBUILD" archive \
  -project "$SOURCE_COPY/ElectronicMail.xcodeproj" \
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
  ELECTRONIC_MAIL_SOURCE_COMMIT=local-working-tree \
  "OTHER_CODE_SIGN_FLAGS=--timestamp"

[ -d "$APP_PATH" ] || fail "archive did not contain Electronic Mail.app"
[ -d "$DSYM_PATH" ] || fail "archive is missing Electronic Mail.app.dSYM"

APP_PATH="$APP_PATH" \
EXPECTED_BACKEND_URL="$BACKEND_URL" \
EXPECTED_VERSION="$VERSION" \
EXPECTED_BUILD_NUMBER="$BUILD_NUMBER" \
EXPECTED_SOURCE_COMMIT=local-working-tree \
EXPECTED_TEAM_ID="$APPLE_DEVELOPMENT_TEAM" \
DSYM_PATH="$DSYM_PATH" \
SIGNING_MODE=developer-id \
INFO_POLICY=local-signed \
REQUIRE_ADHOC_SIGNATURE=0 \
REQUIRE_NOTARIZATION=0 \
bash "$ROOT_DIR/scripts/verify-macos-release.sh"

ditto "$APP_PATH" "$STAGED_INSTALL_PATH"
APP_PATH="$STAGED_INSTALL_PATH" \
EXPECTED_BACKEND_URL="$BACKEND_URL" \
EXPECTED_VERSION="$VERSION" \
EXPECTED_BUILD_NUMBER="$BUILD_NUMBER" \
EXPECTED_SOURCE_COMMIT=local-working-tree \
EXPECTED_TEAM_ID="$APPLE_DEVELOPMENT_TEAM" \
SIGNING_MODE=developer-id \
INFO_POLICY=local-signed \
REQUIRE_ADHOC_SIGNATURE=0 \
REQUIRE_NOTARIZATION=0 \
bash "$ROOT_DIR/scripts/verify-macos-release.sh"

echo "==> Replacing the app while preserving its container, Keychain, and mailbox database"
osascript -e 'tell application id "app.electronicmail.mac" to quit' >/dev/null 2>&1 || true
for _ in {1..50}; do
  if ! pgrep -f '^/Applications/Electronic Mail.app/Contents/MacOS/Electronic Mail$' >/dev/null; then
    break
  fi
  sleep 0.1
done
if pgrep -f '^/Applications/Electronic Mail.app/Contents/MacOS/Electronic Mail$' >/dev/null; then
  fail "Electronic Mail did not quit; the current installed app was not changed"
fi

if [ -d "$INSTALL_PATH" ]; then
  HAD_EXISTING=1
  mv "$INSTALL_PATH" "$BACKUP_PATH"
fi
mv "$STAGED_INSTALL_PATH" "$INSTALL_PATH"
INSTALL_REPLACED=1

APP_PATH="$INSTALL_PATH" \
EXPECTED_BACKEND_URL="$BACKEND_URL" \
EXPECTED_VERSION="$VERSION" \
EXPECTED_BUILD_NUMBER="$BUILD_NUMBER" \
EXPECTED_SOURCE_COMMIT=local-working-tree \
EXPECTED_TEAM_ID="$APPLE_DEVELOPMENT_TEAM" \
SIGNING_MODE=developer-id \
INFO_POLICY=local-signed \
REQUIRE_ADHOC_SIGNATURE=0 \
REQUIRE_NOTARIZATION=0 \
bash "$ROOT_DIR/scripts/verify-macos-release.sh"

open "$INSTALL_PATH"
INSTALL_COMPLETE=1
echo "Installed and opened the stable Developer ID build at $INSTALL_PATH."
