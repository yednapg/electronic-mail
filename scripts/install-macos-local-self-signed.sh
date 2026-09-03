#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VALIDATE_INPUTS_ONLY=0

if [ "${1:-}" = "--validate-inputs" ]; then
  shift
  [ "$#" -eq 0 ] || { echo "local self-signed install error: --validate-inputs accepts no additional arguments" >&2; exit 1; }
  VALIDATE_INPUTS_ONLY=1
fi
[ "$#" -eq 0 ] || { echo "local self-signed install error: unknown argument: $1" >&2; exit 1; }

VERSION="${VERSION:-1.0.0}"
BUILD_NUMBER="${BUILD_NUMBER:-1}"
BACKEND_URL="${BACKEND_URL:-http://localhost:3001}"
CURRENT_YEAR="${CURRENT_YEAR:-$(date +%Y)}"
LOCAL_CODE_SIGN_IDENTITY="${LOCAL_CODE_SIGN_IDENTITY:-Electronic Mail Local Signing}"
INSTALL_PATH="/Applications/Electronic Mail.app"

fail() {
  echo "local self-signed install error: $*" >&2
  exit 1
}

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || fail "VERSION must contain two or three numeric components"
[[ "$BUILD_NUMBER" =~ ^[1-9][0-9]*$ ]] || fail "BUILD_NUMBER must be a positive integer"
[[ "$CURRENT_YEAR" =~ ^20[0-9]{2}$ ]] || fail "CURRENT_YEAR must be a four-digit year"
[ "$BACKEND_URL" = "http://localhost:3001" ] || fail "BACKEND_URL must be exactly http://localhost:3001 for the local self-signed install"
[ -n "$LOCAL_CODE_SIGN_IDENTITY" ] || fail "LOCAL_CODE_SIGN_IDENTITY is required"
[ "$LOCAL_CODE_SIGN_IDENTITY" != "-" ] || fail "an ad-hoc identity is not permitted"
[[ "$LOCAL_CODE_SIGN_IDENTITY" != Developer\ ID\ Application:* ]] || fail "use the separate Developer ID installer for a Developer ID identity"
[[ "$LOCAL_CODE_SIGN_IDENTITY" != *$'\n'* && "$LOCAL_CODE_SIGN_IDENTITY" != *$'\r'* ]] || \
  fail "LOCAL_CODE_SIGN_IDENTITY cannot contain line breaks"

if [ "$VALIDATE_INPUTS_ONLY" = "1" ]; then
  echo "macOS local self-signed inputs are valid: version $VERSION ($BUILD_NUMBER), identity $LOCAL_CODE_SIGN_IDENTITY"
  exit 0
fi

for command in codesign curl ditto lipo osascript pgrep plutil python3 security; do
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

IDENTITY_SHA1="$(security find-identity -v -p codesigning | python3 -c '
import re
import sys

name = sys.argv[1]
matches = []
for line in sys.stdin:
    match = re.match(r"^\s*\d+\)\s+([0-9A-F]{40})\s+\"(.*)\"\s*$", line.rstrip("\n"))
    if match and match.group(2) == name:
        matches.append(match.group(1))
if len(matches) != 1:
    raise SystemExit(1)
print(matches[0])
' "$LOCAL_CODE_SIGN_IDENTITY")" || \
  fail "exactly one valid '$LOCAL_CODE_SIGN_IDENTITY' code-signing identity must be available in the login Keychain; the installed app was not changed"

if [ -d "$INSTALL_PATH" ]; then
  PREVIOUS_IDENTITY_SHA1="$(plutil -extract ElectronicMailLocalSigningIdentitySHA1 raw "$INSTALL_PATH/Contents/Info.plist" 2>/dev/null || true)"
  if [ -n "$PREVIOUS_IDENTITY_SHA1" ] && [ "$PREVIOUS_IDENTITY_SHA1" != "$IDENTITY_SHA1" ]; then
    fail "the installed app was signed by a different local certificate; restore that certificate before replacing the app"
  fi
fi

READY_RESPONSE="$(curl --fail --silent --show-error --max-time 5 "$BACKEND_URL/ready")" || \
  fail "the local backend is not ready at $BACKEND_URL; the installed app was not changed"
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

WORK_DIR="$(mktemp -d /tmp/electronic-mail-local-self-signed.XXXXXX)"
SOURCE_ROOT="$WORK_DIR/source"
SOURCE_COPY="$SOURCE_ROOT/macos/ElectronicMail"
DERIVED_DATA_PATH="$WORK_DIR/DerivedData"
APP_PATH="$DERIVED_DATA_PATH/Build/Products/Release/Electronic Mail.app"
DSYM_PATH="$DERIVED_DATA_PATH/Build/Products/Release/Electronic Mail.app.dSYM"
FRAMEWORK_PATH="$APP_PATH/Contents/Frameworks/ElectronicMailCore.framework"
ENTITLEMENTS_PATH="$SOURCE_COPY/Config/Entitlements/ElectronicMail-LocalSelfSigned.entitlements"
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
    echo "local self-signed install error: installation failed; the previous app was restored" >&2
  fi
  rm -rf "$WORK_DIR"
  exit "$status"
}
trap cleanup_local_install EXIT

verify_local_app() {
  local app_path="$1"
  local executable="$app_path/Contents/MacOS/Electronic Mail"
  local framework="$app_path/Contents/Frameworks/ElectronicMailCore.framework"
  local signature_details
  local requirement
  local actual_architectures

  [ -x "$executable" ] || fail "signed app is missing its executable"
  [ -d "$framework" ] || fail "signed app is missing ElectronicMailCore.framework"
  codesign --verify --deep --strict --verbose=2 "$app_path"
  codesign --verify --strict --verbose=2 "$framework"

  signature_details="$(codesign -dvvv "$app_path" 2>&1)"
  printf '%s\n' "$signature_details" | grep -Fq "Identifier=app.electronicmail.mac" || fail "signed app has the wrong bundle identifier"
  printf '%s\n' "$signature_details" | grep -Fq "Authority=$LOCAL_CODE_SIGN_IDENTITY" || fail "signed app does not use the requested local identity"
  if printf '%s\n' "$signature_details" | grep -Fq "Signature=adhoc"; then
    fail "signed app unexpectedly has an ad-hoc signature"
  fi

  requirement="$(codesign -d -r- "$app_path" 2>&1)"
  printf '%s\n' "$requirement" | grep -Fq 'identifier "app.electronicmail.mac"' || fail "signed app lacks the stable bundle designated requirement"

  [ "$(plutil -extract CFBundleIdentifier raw "$app_path/Contents/Info.plist")" = "app.electronicmail.mac" ] || fail "Info.plist has the wrong bundle identifier"
  [ "$(plutil -extract CFBundleDisplayName raw "$app_path/Contents/Info.plist")" = "Electronic Mail" ] || fail "Info.plist has the wrong display name"
  [ "$(plutil -extract ElectronicMailDistributionChannel raw "$app_path/Contents/Info.plist")" = "local-self-signed" ] || fail "Info.plist lacks the local self-signed marker"
  [ "$(plutil -extract ElectronicMailLocalSigningIdentitySHA1 raw "$app_path/Contents/Info.plist")" = "$IDENTITY_SHA1" ] || fail "Info.plist does not bind the local signing identity"
  [ "$(plutil -extract BackendBaseURL raw "$app_path/Contents/Info.plist")" = "$BACKEND_URL" ] || fail "Info.plist has the wrong backend URL"

  actual_architectures="$(lipo -archs "$executable")"
  printf '%s\n' "$actual_architectures" | grep -qw arm64 || fail "signed app is missing arm64"
  printf '%s\n' "$actual_architectures" | grep -qw x86_64 || fail "signed app is missing x86_64"

  local entitlement_dir
  entitlement_dir="$(mktemp -d "$WORK_DIR/entitlements.XXXXXX")"
  codesign -d --entitlements - --xml "$app_path" > "$entitlement_dir/actual.plist" 2> "$entitlement_dir/codesign.log"
  plutil -lint "$entitlement_dir/actual.plist" >/dev/null
  python3 - "$ENTITLEMENTS_PATH" "$entitlement_dir/actual.plist" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as handle:
    expected = plistlib.load(handle)
with open(sys.argv[2], "rb") as handle:
    actual = plistlib.load(handle)
if actual != expected:
    raise SystemExit(f"signed app entitlements differ from the local policy: {actual}")
if actual.get("com.apple.security.app-sandbox"):
    raise SystemExit("local self-signed app must not use App Sandbox without an Apple Team identity")
PY
}

mkdir -p "$SOURCE_ROOT"
ditto "$ROOT_DIR/macos/ElectronicMail" "$SOURCE_COPY"

python3 - "$SOURCE_COPY/ElectronicMail.xcodeproj/project.pbxproj" <<'PY'
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    project = handle.read()
release = 'INFOPLIST_FILE = "Config/InfoPlists/ElectronicMail-Release-Info.plist";'
local = 'INFOPLIST_FILE = "Config/InfoPlists/ElectronicMail-LocalSelfSigned-Info.plist";'
index = project.find(release)
if index < 0 or project.find(release, index + 1) >= 0:
    raise SystemExit("copied Xcode project did not contain exactly one macOS Release Info.plist setting")
settings_start = project.rfind("buildSettings = {", 0, index)
settings_end = project.find("\n\t\t\t};", index)
if settings_start < 0 or settings_end < 0:
    raise SystemExit("copied Xcode project macOS Release settings block is malformed")
settings_end += len("\n\t\t\t};")
settings = project[settings_start:settings_end]
if settings.count(release) != 1 or local in settings:
    raise SystemExit("copied Xcode project Release Info.plist setting is not uniquely patchable")
settings = settings.replace(release, local, 1)
with open(path, "w", encoding="utf-8", newline="") as handle:
    handle.write(project[:settings_start] + settings + project[settings_end:])
PY

echo "==> Building Electronic Mail $VERSION ($BUILD_NUMBER) for stable local signing"
DEVELOPER_DIR="$LOCAL_DEVELOPER_DIR" "$XCODEBUILD" \
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
  "ELECTRONIC_MAIL_LOCAL_SIGNING_IDENTITY_SHA1=$IDENTITY_SHA1" \
  "MARKETING_VERSION=$VERSION" \
  "CURRENT_PROJECT_VERSION=$BUILD_NUMBER" \
  "CURRENT_YEAR=$CURRENT_YEAR" \
  ELECTRONIC_MAIL_SOURCE_COMMIT=local-working-tree

[ -d "$APP_PATH" ] || fail "Release build did not produce Electronic Mail.app"
[ -d "$DSYM_PATH" ] || fail "Release build did not produce Electronic Mail.app.dSYM"
[ -d "$FRAMEWORK_PATH" ] || fail "Release build did not embed ElectronicMailCore.framework"

echo "==> Signing with the persistent local identity $LOCAL_CODE_SIGN_IDENTITY"
codesign --force --sign "$IDENTITY_SHA1" --options runtime "$FRAMEWORK_PATH"
codesign --force --sign "$IDENTITY_SHA1" --options runtime --entitlements "$ENTITLEMENTS_PATH" "$APP_PATH"
verify_local_app "$APP_PATH"

echo "==> Smoke-testing the signed app"
"$APP_PATH/Contents/MacOS/Electronic Mail" --electronic-mail-beta-launch-smoke

ditto "$APP_PATH" "$STAGED_INSTALL_PATH"
verify_local_app "$STAGED_INSTALL_PATH"
STAGED_REQUIREMENT="$(codesign -d -r- "$STAGED_INSTALL_PATH" 2>&1 | sed -n '/^designated =>/p')"
[ -n "$STAGED_REQUIREMENT" ] || fail "the staged app is missing its designated requirement"

echo "==> Replacing the app while preserving its container, Keychain, and mailbox data"
osascript -e 'tell application id "app.electronicmail.mac" to quit' >/dev/null 2>&1 || true
for _ in {1..50}; do
  if ! pgrep -f '^/Applications/Electronic Mail.app/Contents/MacOS/Electronic Mail$' >/dev/null; then
    break
  fi
  sleep 0.1
done
if pgrep -f '^/Applications/Electronic Mail.app/Contents/MacOS/Electronic Mail$' >/dev/null; then
  fail "Electronic Mail did not quit; the installed app was not changed"
fi

if [ -d "$INSTALL_PATH" ]; then
  HAD_EXISTING=1
  mv "$INSTALL_PATH" "$BACKUP_PATH"
fi
mv "$STAGED_INSTALL_PATH" "$INSTALL_PATH"
INSTALL_REPLACED=1

verify_local_app "$INSTALL_PATH"
INSTALLED_REQUIREMENT="$(codesign -d -r- "$INSTALL_PATH" 2>&1 | sed -n '/^designated =>/p')"
[ "$INSTALLED_REQUIREMENT" = "$STAGED_REQUIREMENT" ] || fail "the installed app's designated requirement changed during replacement"

open "$INSTALL_PATH"
for _ in {1..50}; do
  if pgrep -f '^/Applications/Electronic Mail.app/Contents/MacOS/Electronic Mail$' >/dev/null; then
    INSTALL_COMPLETE=1
    break
  fi
  sleep 0.1
done
[ "$INSTALL_COMPLETE" = "1" ] || fail "the newly installed app did not remain running"

echo "Installed and opened the stable local self-signed build at $INSTALL_PATH."
echo "Signing identity SHA-1: $IDENTITY_SHA1"
