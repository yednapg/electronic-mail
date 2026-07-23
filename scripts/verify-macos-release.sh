#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_PATH="${APP_PATH:-}"
EXPECTED_BACKEND_URL="${EXPECTED_BACKEND_URL:-}"
EXPECTED_VERSION="${EXPECTED_VERSION:-}"
EXPECTED_BUILD_NUMBER="${EXPECTED_BUILD_NUMBER:-}"
EXPECTED_SOURCE_COMMIT="${EXPECTED_SOURCE_COMMIT:-}"
EXPECTED_TEAM_ID="${EXPECTED_TEAM_ID:-}"
DSYM_PATH="${DSYM_PATH:-}"
SIGNING_MODE="${SIGNING_MODE:-identity-free}"
REQUIRE_NOTARIZATION="${REQUIRE_NOTARIZATION:-0}"

SOURCE_ENTITLEMENTS="$ROOT_DIR/macos/ElectronicMail/ElectronicMail/Mac/ElectronicMail.entitlements"
SOURCE_PRIVACY_MANIFEST="$ROOT_DIR/macos/ElectronicMail/ElectronicMail/Mac/PrivacyInfo.xcprivacy"
SOURCE_RELEASE_INFO="$ROOT_DIR/macos/ElectronicMail/Config/InfoPlists/ElectronicMail-Release-Info.plist"
SOURCE_PROJECT_MANIFEST="$ROOT_DIR/macos/ElectronicMail/Project.swift"
SOURCE_XCODE_PROJECT="$ROOT_DIR/macos/ElectronicMail/ElectronicMail.xcodeproj/project.pbxproj"
SOURCE_DEMO_CLIENT="$ROOT_DIR/macos/ElectronicMail/ElectronicMail/Core/DemoAppClient.swift"

fail() {
  echo "macOS release verification failed: $*" >&2
  exit 1
}

[ -d "$APP_PATH" ] || fail "APP_PATH must point to ElectronicMail.app"
[ -n "$EXPECTED_BACKEND_URL" ] || fail "EXPECTED_BACKEND_URL is required"
[ -n "$EXPECTED_VERSION" ] || fail "EXPECTED_VERSION is required"
[ -n "$EXPECTED_BUILD_NUMBER" ] || fail "EXPECTED_BUILD_NUMBER is required"
[ -n "$EXPECTED_SOURCE_COMMIT" ] || fail "EXPECTED_SOURCE_COMMIT is required"
if [ "$EXPECTED_SOURCE_COMMIT" != "local" ] && [[ ! "$EXPECTED_SOURCE_COMMIT" =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]]; then
  fail "EXPECTED_SOURCE_COMMIT must be 'local' or a full lowercase 40- or 64-character Git commit SHA"
fi
case "$SIGNING_MODE" in
  identity-free | developer-id) ;;
  *) fail "SIGNING_MODE must be identity-free or developer-id" ;;
esac
case "$REQUIRE_NOTARIZATION" in
  0 | 1) ;;
  *) fail "REQUIRE_NOTARIZATION must be 0 or 1" ;;
esac
if [ "$SIGNING_MODE" = "developer-id" ]; then
  [ -n "$EXPECTED_TEAM_ID" ] || fail "EXPECTED_TEAM_ID is required for Developer ID verification"
  [ "$EXPECTED_SOURCE_COMMIT" != "local" ] || fail "Developer ID artifacts must be bound to a Git commit, not the local development marker"
fi
if [ "$REQUIRE_NOTARIZATION" = "1" ] && [ "$SIGNING_MODE" != "developer-id" ]; then
  fail "notarization cannot be required for an identity-free artifact"
fi

for command in codesign grep lipo plutil python3 xcrun; do
  command -v "$command" >/dev/null 2>&1 || fail "required command not found: $command"
done

INFO_PLIST="$APP_PATH/Contents/Info.plist"
EXECUTABLE="$APP_PATH/Contents/MacOS/ElectronicMail"
PRIVACY_MANIFEST="$APP_PATH/Contents/Resources/PrivacyInfo.xcprivacy"
APP_ICON="$APP_PATH/Contents/Resources/AppIcon.icns"
FRAMEWORK="$APP_PATH/Contents/Frameworks/ElectronicMailCore.framework"
FRAMEWORK_EXECUTABLE="$FRAMEWORK/ElectronicMailCore"
FRAMEWORK_PRIVACY_MANIFEST="$FRAMEWORK/Resources/PrivacyInfo.xcprivacy"

[ -s "$INFO_PLIST" ] || fail "Info.plist is missing"
[ -x "$EXECUTABLE" ] || fail "main executable is missing"
[ -s "$PRIVACY_MANIFEST" ] || fail "PrivacyInfo.xcprivacy is missing"
[ -s "$APP_ICON" ] || fail "AppIcon.icns is missing"
[ -d "$FRAMEWORK" ] || fail "ElectronicMailCore.framework is missing"
[ -x "$FRAMEWORK_EXECUTABLE" ] || fail "ElectronicMailCore framework executable is missing"
[ -s "$FRAMEWORK_PRIVACY_MANIFEST" ] || fail "ElectronicMailCore privacy manifest is missing"

for marker in demo-session-token demo@example.com demo-google-today DemoAppFixtures; do
  for executable in "$EXECUTABLE" "$FRAMEWORK_EXECUTABLE"; do
    if LC_ALL=C grep -aFq -- "$marker" "$executable"; then
      fail "Release binary contains Debug-only demo fixture marker '$marker': $executable"
    fi
  done
done

plutil -lint "$SOURCE_ENTITLEMENTS" >/dev/null
plutil -lint "$SOURCE_PRIVACY_MANIFEST" >/dev/null
plutil -lint "$SOURCE_RELEASE_INFO" >/dev/null
plutil -lint "$INFO_PLIST" >/dev/null
plutil -lint "$PRIVACY_MANIFEST" >/dev/null
plutil -lint "$FRAMEWORK_PRIVACY_MANIFEST" >/dev/null

ACTUAL_BACKEND_URL="$(plutil -extract BackendBaseURL raw "$INFO_PLIST")"
ACTUAL_VERSION="$(plutil -extract CFBundleShortVersionString raw "$INFO_PLIST")"
ACTUAL_BUILD_NUMBER="$(plutil -extract CFBundleVersion raw "$INFO_PLIST")"
ACTUAL_SOURCE_COMMIT="$(plutil -extract ElectronicMailSourceCommit raw "$INFO_PLIST")"
ACTUAL_BUNDLE_ID="$(plutil -extract CFBundleIdentifier raw "$INFO_PLIST")"
ACTUAL_MINIMUM_SYSTEM="$(plutil -extract LSMinimumSystemVersion raw "$INFO_PLIST")"

[ "$ACTUAL_BACKEND_URL" = "$EXPECTED_BACKEND_URL" ] || fail "backend URL mismatch: expected $EXPECTED_BACKEND_URL, found $ACTUAL_BACKEND_URL"
[ "$ACTUAL_VERSION" = "$EXPECTED_VERSION" ] || fail "version mismatch: expected $EXPECTED_VERSION, found $ACTUAL_VERSION"
[ "$ACTUAL_BUILD_NUMBER" = "$EXPECTED_BUILD_NUMBER" ] || fail "build-number mismatch: expected $EXPECTED_BUILD_NUMBER, found $ACTUAL_BUILD_NUMBER"
[ "$ACTUAL_SOURCE_COMMIT" = "$EXPECTED_SOURCE_COMMIT" ] || fail "source-commit mismatch: expected $EXPECTED_SOURCE_COMMIT, found $ACTUAL_SOURCE_COMMIT"
[ "$ACTUAL_BUNDLE_ID" = "app.electronicmail.mac" ] || fail "unexpected bundle identifier: $ACTUAL_BUNDLE_ID"
[ "$ACTUAL_MINIMUM_SYSTEM" = "14.0" ] || fail "unexpected minimum macOS version: $ACTUAL_MINIMUM_SYSTEM"

ARCHITECTURES="$(lipo -archs "$EXECUTABLE")"
case " $ARCHITECTURES " in *" arm64 "*) ;; *) fail "main executable is missing arm64" ;; esac
case " $ARCHITECTURES " in *" x86_64 "*) ;; *) fail "main executable is missing x86_64" ;; esac

FRAMEWORK_ARCHITECTURES="$(lipo -archs "$FRAMEWORK_EXECUTABLE")"
case " $FRAMEWORK_ARCHITECTURES " in *" arm64 "*) ;; *) fail "ElectronicMailCore is missing arm64" ;; esac
case " $FRAMEWORK_ARCHITECTURES " in *" x86_64 "*) ;; *) fail "ElectronicMailCore is missing x86_64" ;; esac

if [ -n "$DSYM_PATH" ]; then
  command -v dwarfdump >/dev/null 2>&1 || fail "required command not found: dwarfdump"
  DSYM_EXECUTABLE="$DSYM_PATH/Contents/Resources/DWARF/ElectronicMail"
  [ -s "$DSYM_EXECUTABLE" ] || fail "ElectronicMail dSYM executable is missing"
  DSYM_ARCHITECTURES="$(lipo -archs "$DSYM_EXECUTABLE")"
  case " $DSYM_ARCHITECTURES " in *" arm64 "*) ;; *) fail "ElectronicMail dSYM is missing arm64" ;; esac
  case " $DSYM_ARCHITECTURES " in *" x86_64 "*) ;; *) fail "ElectronicMail dSYM is missing x86_64" ;; esac
  EXECUTABLE_UUIDS="$(dwarfdump --uuid "$EXECUTABLE" | awk '{print $2, $3}' | sort)"
  DSYM_UUIDS="$(dwarfdump --uuid "$DSYM_PATH" | awk '{print $2, $3}' | sort)"
  [ -n "$EXECUTABLE_UUIDS" ] || fail "main executable UUIDs could not be read"
  [ "$EXECUTABLE_UUIDS" = "$DSYM_UUIDS" ] || fail "dSYM UUIDs do not match the packaged executable"
fi

python3 - "$SOURCE_ENTITLEMENTS" "$SOURCE_PRIVACY_MANIFEST" "$SOURCE_RELEASE_INFO" "$SOURCE_PROJECT_MANIFEST" "$SOURCE_XCODE_PROJECT" "$SOURCE_DEMO_CLIENT" "$PRIVACY_MANIFEST" "$FRAMEWORK_PRIVACY_MANIFEST" "$INFO_PLIST" <<'PY'
import plistlib
import re
import sys

(
    source_entitlements_path,
    source_privacy_path,
    source_info_path,
    source_project_manifest_path,
    source_xcode_project_path,
    source_demo_client_path,
    packaged_privacy_path,
    framework_privacy_path,
    packaged_info_path,
) = sys.argv[1:]

def load(path):
    with open(path, "rb") as handle:
        return plistlib.load(handle)

def require(condition, message):
    if not condition:
        raise SystemExit(message)

with open(source_project_manifest_path, encoding="utf-8") as handle:
    project_manifest = handle.read()
release_settings = [
    body
    for body in re.findall(
        r'\.release\(name:\s*"Release",\s*settings:\s*\[(.*?)\]\)',
        project_manifest,
        re.DOTALL,
    )
    if "ELECTRONIC_MAIL_BACKEND_URL" in body
    and "ElectronicMail-Release-Info.plist" in body
]
require(len(release_settings) == 1, "Project.swift must define exactly one macOS app Release settings block")
require(
    '"ENABLE_PREVIEWS": "NO"' in release_settings[0],
    "Project.swift must disable previews for the macOS app Release configuration",
)

with open(source_xcode_project_path, encoding="utf-8") as handle:
    xcode_project = handle.read()
generated_release_settings = [
    body
    for body in re.findall(r"buildSettings = \{(.*?)\n\s*\};", xcode_project, re.DOTALL)
    if "ELECTRONIC_MAIL_BACKEND_URL = \"https://electronic-mail-backend.invalid\";" in body
    and 'INFOPLIST_FILE = "Config/InfoPlists/ElectronicMail-Release-Info.plist";' in body
]
require(
    len(generated_release_settings) == 1,
    "generated Xcode project must define exactly one macOS app Release settings block",
)
require(
    "ENABLE_PREVIEWS = NO;" in generated_release_settings[0]
    and "ENABLE_PREVIEWS = YES;" not in generated_release_settings[0],
    "generated Xcode project must disable previews for the macOS app Release configuration",
)

with open(source_demo_client_path, encoding="utf-8") as handle:
    demo_source_lines = [line.strip() for line in handle if line.strip()]
require(demo_source_lines, "DemoAppClient.swift is empty")
require(
    demo_source_lines[0] == "#if DEBUG" and demo_source_lines[-1] == "#endif",
    "DemoAppClient.swift must be entirely guarded by #if DEBUG",
)
conditional_depth = 0
for index, line in enumerate(demo_source_lines):
    if line.startswith("#if "):
        conditional_depth += 1
    elif line == "#endif":
        conditional_depth -= 1
        require(conditional_depth >= 0, "DemoAppClient.swift has an unmatched #endif")
        require(
            conditional_depth > 0 or index == len(demo_source_lines) - 1,
            "DemoAppClient.swift closes its outer #if DEBUG guard before end of file",
        )
require(conditional_depth == 0, "DemoAppClient.swift has an unterminated conditional compilation block")

entitlements = load(source_entitlements_path)
expected_entitlements = {
    "com.apple.security.app-sandbox": True,
    "com.apple.security.network.client": True,
    "com.apple.security.files.user-selected.read-write": True,
}
require(entitlements == expected_entitlements, f"source entitlements differ from the approved minimal set: {entitlements}")

source_info = load(source_info_path)
packaged_info = load(packaged_info_path)
require(source_info.get("BackendBaseURL") == "$(ELECTRONIC_MAIL_BACKEND_URL)", "Release Info.plist must use backend build-setting injection")
require(source_info.get("ElectronicMailSourceCommit") == "$(ELECTRONIC_MAIL_SOURCE_COMMIT)", "Release Info.plist must use source-commit build-setting injection")
require("NSAppTransportSecurity" not in source_info, "Release Info.plist must not contain local or insecure ATS exceptions")
require("NSAppTransportSecurity" not in packaged_info, "packaged Release app contains an ATS exception")
require(any("electronicmail" in item.get("CFBundleURLSchemes", []) for item in packaged_info.get("CFBundleURLTypes", [])), "OAuth callback URL scheme is missing")
copyright_text = packaged_info.get("NSHumanReadableCopyright", "")
require("$(" not in copyright_text and re.search(r"\b20\d{2}\b", copyright_text), "copyright year was not expanded")

def validate_privacy(path):
    privacy = load(path)
    expected_root_keys = {
        "NSPrivacyAccessedAPITypes",
        "NSPrivacyCollectedDataTypes",
        "NSPrivacyTracking",
        "NSPrivacyTrackingDomains",
    }
    require(set(privacy) == expected_root_keys, f"{path}: privacy-manifest root keys are incomplete or unexpected")
    require(privacy.get("NSPrivacyTracking") is False, f"{path}: tracking must be false")
    require(privacy.get("NSPrivacyTrackingDomains") == [], f"{path}: tracking domains must be empty")
    expected_types = {
        "NSPrivacyCollectedDataTypeName",
        "NSPrivacyCollectedDataTypeEmailAddress",
        "NSPrivacyCollectedDataTypeUserID",
        "NSPrivacyCollectedDataTypeEmailsOrTextMessages",
        "NSPrivacyCollectedDataTypePhotosorVideos",
        "NSPrivacyCollectedDataTypeAudioData",
        "NSPrivacyCollectedDataTypeOtherUserContent",
        "NSPrivacyCollectedDataTypeSearchHistory",
        "NSPrivacyCollectedDataTypeProductInteraction",
        "NSPrivacyCollectedDataTypeOtherDiagnosticData",
        "NSPrivacyCollectedDataTypeOtherDataTypes",
    }
    collected_items = privacy.get("NSPrivacyCollectedDataTypes", [])
    require(isinstance(collected_items, list), f"{path}: collected-data declarations must be an array")
    collected = {item.get("NSPrivacyCollectedDataType"): item for item in collected_items if isinstance(item, dict)}
    require(len(collected_items) == len(collected), f"{path}: collected-data declarations contain a duplicate or malformed entry")
    require(set(collected) == expected_types, f"{path}: collected-data declarations are incomplete or unexpected")
    for data_type, item in collected.items():
        expected_item = {
            "NSPrivacyCollectedDataType": data_type,
            "NSPrivacyCollectedDataTypeLinked": True,
            "NSPrivacyCollectedDataTypePurposes": ["NSPrivacyCollectedDataTypePurposeAppFunctionality"],
            "NSPrivacyCollectedDataTypeTracking": False,
        }
        require(item == expected_item, f"{path}: unexpected declaration for {data_type}: {item}")

    accessed_items = privacy.get("NSPrivacyAccessedAPITypes", [])
    require(isinstance(accessed_items, list), f"{path}: accessed-API declarations must be an array")
    accessed = {item.get("NSPrivacyAccessedAPIType"): item for item in accessed_items if isinstance(item, dict)}
    require(len(accessed_items) == len(accessed), f"{path}: accessed-API declarations contain a duplicate or malformed entry")
    expected_accessed = {
        "NSPrivacyAccessedAPICategoryUserDefaults": {
            "NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategoryUserDefaults",
            "NSPrivacyAccessedAPITypeReasons": ["CA92.1"],
        }
    }
    require(accessed == expected_accessed, f"{path}: accessed-API declarations differ from the audited set: {accessed}")

validate_privacy(source_privacy_path)
validate_privacy(packaged_privacy_path)
validate_privacy(framework_privacy_path)
require(load(source_privacy_path) == load(packaged_privacy_path), "packaged privacy manifest differs from the reviewed source manifest")
require(load(source_privacy_path) == load(framework_privacy_path), "ElectronicMailCore privacy manifest differs from the reviewed source manifest")
PY

if [ "$SIGNING_MODE" = "identity-free" ]; then
  for code_path in "$APP_PATH" "$FRAMEWORK"; do
    SIGNATURE_DETAILS="$(codesign -dvvv "$code_path" 2>&1 || true)"
    if printf '%s\n' "$SIGNATURE_DETAILS" | grep -q '^Authority='; then
      fail "identity-free preflight unexpectedly contains a certificate authority: $code_path"
    fi
    ACTUAL_TEAM_ID="$(printf '%s\n' "$SIGNATURE_DETAILS" | sed -n 's/^TeamIdentifier=//p' | head -1)"
    if [ -n "$ACTUAL_TEAM_ID" ] && [ "$ACTUAL_TEAM_ID" != "not set" ]; then
      fail "identity-free preflight unexpectedly contains TeamIdentifier $ACTUAL_TEAM_ID: $code_path"
    fi
    if ! printf '%s\n' "$SIGNATURE_DETAILS" | grep -Eq '^(Signature=adhoc|.*code object is not signed at all)'; then
      fail "identity-free preflight has an unexpected signature state: $code_path"
    fi
  done
fi

if [ "$SIGNING_MODE" = "developer-id" ]; then
  codesign --verify --deep --strict --verbose=2 "$APP_PATH"
  codesign --verify --strict --verbose=2 "$FRAMEWORK"

  for code_path in "$APP_PATH" "$FRAMEWORK"; do
    SIGNATURE_DETAILS="$(codesign -dvvv "$code_path" 2>&1)"
    printf '%s\n' "$SIGNATURE_DETAILS" | grep -q '^Authority=Developer ID Application:' || fail "code is not signed with a Developer ID Application certificate: $code_path"
    printf '%s\n' "$SIGNATURE_DETAILS" | grep -q '^Timestamp=' || fail "secure signing timestamp is missing: $code_path"
    ACTUAL_TEAM_ID="$(printf '%s\n' "$SIGNATURE_DETAILS" | sed -n 's/^TeamIdentifier=//p' | head -1)"
    [ "$ACTUAL_TEAM_ID" = "$EXPECTED_TEAM_ID" ] || fail "signature team mismatch: expected $EXPECTED_TEAM_ID, found ${ACTUAL_TEAM_ID:-none}: $code_path"
  done

  SIGNATURE_DETAILS="$(codesign -dvvv "$APP_PATH" 2>&1)"
  printf '%s\n' "$SIGNATURE_DETAILS" | grep -q 'flags=.*runtime' || fail "Hardened Runtime is missing from the app"

  TMP_DIR="$(mktemp -d)"
  trap 'rm -rf "$TMP_DIR"' EXIT
  codesign -d --entitlements - --xml "$APP_PATH" > "$TMP_DIR/embedded-entitlements.plist" 2> "$TMP_DIR/codesign-entitlements.log"
  plutil -lint "$TMP_DIR/embedded-entitlements.plist" >/dev/null

  python3 - "$TMP_DIR/embedded-entitlements.plist" "$EXPECTED_TEAM_ID" "$ACTUAL_BUNDLE_ID" <<'PY'
import plistlib
import sys

path, expected_team, bundle_id = sys.argv[1:]
with open(path, "rb") as handle:
    entitlements = plistlib.load(handle)

def require(condition, message):
    if not condition:
        raise SystemExit(message)

required = {
    "com.apple.security.app-sandbox": True,
    "com.apple.security.network.client": True,
    "com.apple.security.files.user-selected.read-write": True,
}
for key, value in required.items():
    require(entitlements.get(key) is value, f"signed app is missing required entitlement {key}")

forbidden_true = [
    "com.apple.security.get-task-allow",
    "com.apple.security.network.server",
    "com.apple.security.cs.allow-jit",
    "com.apple.security.cs.allow-unsigned-executable-memory",
    "com.apple.security.cs.disable-library-validation",
    "com.apple.security.cs.allow-dyld-environment-variables",
]
for key in forbidden_true:
    require(entitlements.get(key) is not True, f"signed app contains forbidden entitlement {key}")

expected_app_id = f"{expected_team}.{bundle_id}"
require(entitlements.get("com.apple.application-identifier") == expected_app_id, "signed app is missing the team-bound application identifier required by Data Protection Keychain")
require(entitlements.get("com.apple.developer.team-identifier") == expected_team, "signed app is missing the expected developer team identifier")

explicit_groups = entitlements.get("keychain-access-groups")
if explicit_groups is not None:
    require(isinstance(explicit_groups, list) and expected_app_id in explicit_groups, "explicit Keychain groups do not include the app's private access group")

allowed_keys = set(required) | {
    "com.apple.application-identifier",
    "com.apple.developer.team-identifier",
    "keychain-access-groups",
}
unexpected_keys = set(entitlements) - allowed_keys
require(not unexpected_keys, f"signed app contains unapproved entitlements: {sorted(unexpected_keys)}")
PY

  if [ "$REQUIRE_NOTARIZATION" = "1" ]; then
    command -v spctl >/dev/null 2>&1 || fail "required command not found: spctl"
    xcrun stapler validate "$APP_PATH"
    spctl --assess --type execute --verbose=2 "$APP_PATH"
  fi
fi

echo "macOS app verification passed ($SIGNING_MODE, architectures: $ARCHITECTURES)."
