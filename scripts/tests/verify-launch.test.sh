#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT_DIR/scripts/verify-launch.sh"
COMMAND_STUB="$ROOT_DIR/scripts/tests/fixtures/launch-command-stub.sh"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/electronic-mail-launch-verify-test.XXXXXX")"
trap 'rm -rf "$TEST_DIR"' EXIT

RELEASE_SHA=0123456789abcdef0123456789abcdef01234567
VERSION=1.2.3
BUILD_NUMBER=456
BACKEND_ORIGIN=https://api.launch-test.electronicmail.dev
WEB_ORIGIN=https://www.launch-test.electronicmail.dev
EVIDENCE_ORIGIN=https://evidence.electronicmail.dev
TEAM_ID=ABCD123456
XCODE_VERSION=16.4
XCODE_BUILD=16F6
DEFAULT_QUEUE_DEPTH_JSON='{"critical":1,"default":2}'

fail() {
  echo "verify-launch test failed: $*" >&2
  exit 1
}

create_app() {
  local app_path="$1"
  local version="$2"
  local build_number="$3"
  local backend_origin="$4"
  local source_commit="${5:-$RELEASE_SHA}"

  mkdir -p \
    "$app_path/Contents/MacOS" \
    "$app_path/Contents/Resources" \
    "$app_path/Contents/Frameworks/ElectronicMailCore.framework/Resources"
  cat > "$app_path/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>BackendBaseURL</key><string>$backend_origin</string>
  <key>ElectronicMailSourceCommit</key><string>$source_commit</string>
  <key>CFBundleIdentifier</key><string>app.electronicmail.mac</string>
  <key>CFBundleShortVersionString</key><string>$version</string>
  <key>CFBundleVersion</key><string>$build_number</string>
  <key>CFBundleURLTypes</key><array><dict>
    <key>CFBundleURLSchemes</key><array><string>electronicmail</string></array>
  </dict></array>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHumanReadableCopyright</key><string>Copyright 2026 Electronic Mail</string>
</dict></plist>
EOF
  printf '%s\n' 'test universal executable' > "$app_path/Contents/MacOS/ElectronicMail"
  printf '%s\n' 'test universal framework' > "$app_path/Contents/Frameworks/ElectronicMailCore.framework/ElectronicMailCore"
  printf '%s\n' 'test app icon' > "$app_path/Contents/Resources/AppIcon.icns"
  chmod +x \
    "$app_path/Contents/MacOS/ElectronicMail" \
    "$app_path/Contents/Frameworks/ElectronicMailCore.framework/ElectronicMailCore"
  cp \
    "$ROOT_DIR/macos/ElectronicMail/ElectronicMail/Mac/PrivacyInfo.xcprivacy" \
    "$app_path/Contents/Resources/PrivacyInfo.xcprivacy"
  cp \
    "$ROOT_DIR/macos/ElectronicMail/ElectronicMail/Mac/PrivacyInfo.xcprivacy" \
    "$app_path/Contents/Frameworks/ElectronicMailCore.framework/Resources/PrivacyInfo.xcprivacy"
}

write_metadata() {
  local version="$1"
  local xcode_build="${2:-$XCODE_BUILD}"
  local created_at
  created_at="$(python3 - <<'PY'
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc) - timedelta(hours=3)).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
PY
)"
  cat > "$METADATA_PATH" <<EOF
{
  "backend_origin": "$BACKEND_ORIGIN",
  "build_number": "$BUILD_NUMBER",
  "bundle_identifier": "app.electronicmail.mac",
  "developer_team": "$TEAM_ID",
  "created_at_utc": "$created_at",
  "notarized": true,
  "product": "Electronic Mail",
  "source_commit": "$RELEASE_SHA",
  "source_tree_clean": true,
  "version": "$version",
  "xcode_version": "$XCODE_VERSION",
  "xcode_build": "$xcode_build"
}
EOF
}

write_checksums() {
  (
    cd "$ARTIFACT_DIR"
    shasum -a 256 "$(basename "$DMG_PATH")" "$(basename "$METADATA_PATH")" > "$(basename "$CHECKSUM_PATH")"
  )
}

write_acceptance() {
  local build_number="${1:-$BUILD_NUMBER}"
  local manual_status="${2:-pass}"
  local dmg_sha256
  dmg_sha256="$(shasum -a 256 "$DMG_PATH" | awk '{print $1}')"
  python3 - \
    "$ACCEPTANCE_PATH" "$RELEASE_SHA" "$dmg_sha256" "$VERSION" "$build_number" \
    "$BACKEND_ORIGIN" "$WEB_ORIGIN" "$manual_status" <<'PY'
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sys

path, release, dmg_sha256, version, build, backend, web, manual_status = sys.argv[1:]
now = datetime.now(timezone.utc).replace(microsecond=0)
tested_at = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
approved_at = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
completed_at = now.isoformat().replace("+00:00", "Z")
manual_ids = (
    "install", "auth", "full_sync", "compose", "draft", "conversation", "actions",
    "search", "attachments", "sync", "scale", "failure", "security", "accessibility", "update",
)
approval_ids = (
    "product_owner", "privacy_legal", "google_oauth_security", "support_on_call", "production_operations",
)

def evidence_record(url):
    body = f"evidence:{url}\n".encode("utf-8")
    return {"url": url, "sha256": hashlib.sha256(body).hexdigest()}

payload = {
    "schema_version": 2,
    "product": "Electronic Mail",
    "release": {
        "source_commit": release,
        "dmg_sha256": dmg_sha256,
        "version": version,
        "build_number": build,
        "backend_origin": backend,
        "web_origin": web,
    },
    "completed_at": completed_at,
    "manual_tests": [
        {
            "id": test_id,
            "status": manual_status,
            "tester": "launch.tester@electronicmail.dev",
            "tested_at": tested_at,
            "test_account": "redacted launch account",
            "environment": "Clean launch-test Mac on macOS 15",
            "evidence": [evidence_record(f"https://evidence.electronicmail.dev/tests/{test_id}")],
            "notes": "",
        }
        for test_id in manual_ids
    ],
    "approvals": [
        {
            "id": approval_id,
            "status": "approved",
            "approver": "launch.approver@electronicmail.dev",
            "approved_at": approved_at,
            "evidence": evidence_record(f"https://evidence.electronicmail.dev/approvals/{approval_id}"),
            "notes": "",
        }
        for approval_id in approval_ids
    ],
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
PY
}

run_verify() {
  PATH="$FAKE_BIN:$ORIGINAL_PATH" \
  LAUNCH_VERIFY_MODE="${LAUNCH_TEST_MODE-production}" \
  BACKEND_URL="${LAUNCH_TEST_BACKEND_URL-$BACKEND_ORIGIN}" \
  WEB_URL="${LAUNCH_TEST_WEB_URL-$WEB_ORIGIN}" \
  OPS_BEARER_TOKEN="${LAUNCH_TEST_OPS_TOKEN-launch-test-token}" \
  APPLE_DEVELOPMENT_TEAM="${LAUNCH_TEST_TEAM-$TEAM_ID}" \
  APP_PATH="${LAUNCH_TEST_APP_PATH-$SUPPLIED_APP_PATH}" \
  DMG_PATH="${LAUNCH_TEST_DMG_PATH-$DMG_PATH}" \
  EXPECTED_RELEASE_SHA="${LAUNCH_TEST_EXPECTED_RELEASE_SHA-$RELEASE_SHA}" \
  EXPECTED_VERSION="${LAUNCH_TEST_EXPECTED_VERSION-$VERSION}" \
  EXPECTED_BUILD_NUMBER="${LAUNCH_TEST_EXPECTED_BUILD_NUMBER-$BUILD_NUMBER}" \
  RELEASE_METADATA_PATH="${LAUNCH_TEST_METADATA_PATH-$METADATA_PATH}" \
  SHA256SUMS_PATH="${LAUNCH_TEST_CHECKSUM_PATH-$CHECKSUM_PATH}" \
  LAUNCH_ACCEPTANCE_PATH="${LAUNCH_TEST_ACCEPTANCE_PATH-$ACCEPTANCE_PATH}" \
  EVIDENCE_ORIGIN="${LAUNCH_TEST_EVIDENCE_ORIGIN-$EVIDENCE_ORIGIN}" \
  EVIDENCE_BEARER_TOKEN="${LAUNCH_TEST_EVIDENCE_TOKEN-launch-evidence-test-token}" \
  SKIP_OPS_HEALTH="${LAUNCH_TEST_SKIP_OPS:-0}" \
  SKIP_ARTIFACT_VERIFY="${LAUNCH_TEST_SKIP_ARTIFACT:-0}" \
  SKIP_PUBLIC_PAGE_VERIFY="${LAUNCH_TEST_SKIP_PUBLIC:-0}" \
  SKIP_ACCEPTANCE_VERIFY="${LAUNCH_TEST_SKIP_ACCEPTANCE:-0}" \
  ALLOW_INSECURE_LAUNCH_VERIFY="${LAUNCH_TEST_ALLOW_INSECURE:-0}" \
  MAX_QUEUE_DEPTH_PER_QUEUE="${LAUNCH_TEST_MAX_PER_QUEUE:-5}" \
  MAX_TOTAL_QUEUE_DEPTH="${LAUNCH_TEST_MAX_TOTAL:-10}" \
  MAX_OLDEST_QUEUED_AGE_SECONDS="${LAUNCH_TEST_MAX_AGE:-30}" \
  LAUNCH_TEST_RELEASE_SHA="$RELEASE_SHA" \
  LAUNCH_TEST_TEAM_ID="$TEAM_ID" \
  LAUNCH_TEST_DMG_APP_PATH="${LAUNCH_TEST_DMG_APP_OVERRIDE-$DMG_APP_PATH}" \
  LAUNCH_TEST_HEALTH_RELEASE="${LAUNCH_TEST_HEALTH_RELEASE_OVERRIDE-$RELEASE_SHA}" \
  LAUNCH_TEST_READY_RELEASE="${LAUNCH_TEST_READY_RELEASE_OVERRIDE-$RELEASE_SHA}" \
  LAUNCH_TEST_READY_ENVIRONMENT="${LAUNCH_TEST_READY_ENVIRONMENT_OVERRIDE-production}" \
  LAUNCH_TEST_OPS_RELEASE="${LAUNCH_TEST_OPS_RELEASE_OVERRIDE-$RELEASE_SHA}" \
  LAUNCH_TEST_OPS_ENVIRONMENT="${LAUNCH_TEST_OPS_ENVIRONMENT_OVERRIDE-production}" \
  LAUNCH_TEST_QUEUE_DEPTH_JSON="${LAUNCH_TEST_QUEUE_DEPTH_OVERRIDE-$DEFAULT_QUEUE_DEPTH_JSON}" \
  LAUNCH_TEST_DEAD_JOBS="${LAUNCH_TEST_DEAD_JOBS_OVERRIDE-0}" \
  LAUNCH_TEST_STALE_JOBS="${LAUNCH_TEST_STALE_JOBS_OVERRIDE-0}" \
  LAUNCH_TEST_OLDEST_QUEUED_AGE="${LAUNCH_TEST_OLDEST_QUEUED_AGE_OVERRIDE-10}" \
  LAUNCH_TEST_WORKER_RELEASES_MATCH="${LAUNCH_TEST_WORKER_RELEASES_MATCH_OVERRIDE-true}" \
  LAUNCH_TEST_DOWNLOAD_BODY="${LAUNCH_TEST_DOWNLOAD_BODY_OVERRIDE-deterministic fake DMG}" \
  bash "$SCRIPT"
}

expect_failure() {
  local label="$1"
  local expected_message="$2"
  local stdout_path="$TEST_DIR/$label.stdout"
  local stderr_path="$TEST_DIR/$label.stderr"
  local status
  shift 2

  set +e
  "$@" > "$stdout_path" 2> "$stderr_path"
  status=$?
  set -e
  [ "$status" -ne 0 ] || fail "$label unexpectedly passed"
  grep -Fq "$expected_message" "$stderr_path" || {
    echo "--- $label stderr ---" >&2
    sed -n '1,160p' "$stderr_path" >&2
    fail "$label did not report: $expected_message"
  }
}

ORIGINAL_PATH="$PATH"
FAKE_BIN="$TEST_DIR/bin"
ARTIFACT_DIR="$TEST_DIR/artifacts"
SUPPLIED_APP_PATH="$TEST_DIR/supplied/ElectronicMail.app"
DMG_APP_PATH="$TEST_DIR/dmg-source/ElectronicMail.app"
MISMATCHED_DMG_APP_PATH="$TEST_DIR/mismatched-dmg-source/ElectronicMail.app"
MISMATCHED_COMMIT_APP_PATH="$TEST_DIR/mismatched-commit/ElectronicMail.app"
DMG_PATH="$ARTIFACT_DIR/ElectronicMail-$VERSION-$BUILD_NUMBER.dmg"
METADATA_PATH="$ARTIFACT_DIR/RELEASE-METADATA-$VERSION-$BUILD_NUMBER.json"
CHECKSUM_PATH="$ARTIFACT_DIR/SHA256SUMS-$VERSION-$BUILD_NUMBER.txt"
ACCEPTANCE_PATH="$TEST_DIR/launch-acceptance.json"

mkdir -p "$FAKE_BIN" "$ARTIFACT_DIR"
for command in curl hdiutil codesign lipo spctl xcrun; do
  cp "$COMMAND_STUB" "$FAKE_BIN/$command"
  chmod +x "$FAKE_BIN/$command"
done
create_app "$SUPPLIED_APP_PATH" "$VERSION" "$BUILD_NUMBER" "$BACKEND_ORIGIN"
create_app "$DMG_APP_PATH" "$VERSION" "$BUILD_NUMBER" "$BACKEND_ORIGIN"
create_app "$MISMATCHED_DMG_APP_PATH" 9.9.9 "$BUILD_NUMBER" "$BACKEND_ORIGIN"
create_app "$MISMATCHED_COMMIT_APP_PATH" "$VERSION" "$BUILD_NUMBER" "$BACKEND_ORIGIN" aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
printf '%s\n' 'deterministic fake DMG' > "$DMG_PATH"
write_metadata "$VERSION"
write_checksums
write_acceptance

LAUNCH_TEST_EXPECTED_RELEASE_SHA='' \
  expect_failure missing-release-sha 'EXPECTED_RELEASE_SHA is required for production launch verification' run_verify

LAUNCH_TEST_SKIP_ARTIFACT=1 \
  expect_failure production-skip 'SKIP_ARTIFACT_VERIFY is only allowed with LAUNCH_VERIFY_MODE=non-production' run_verify

LAUNCH_TEST_SKIP_ACCEPTANCE=1 \
  expect_failure production-acceptance-skip 'SKIP_ACCEPTANCE_VERIFY is only allowed with LAUNCH_VERIFY_MODE=non-production' run_verify

LAUNCH_TEST_ACCEPTANCE_PATH='' \
  expect_failure missing-acceptance 'LAUNCH_ACCEPTANCE_PATH is required for production launch verification' run_verify

LAUNCH_TEST_EVIDENCE_TOKEN='' \
  expect_failure missing-evidence-token 'EVIDENCE_BEARER_TOKEN is required for production launch verification' run_verify

write_acceptance 455
expect_failure acceptance-build-mismatch "manifest release build_number mismatch: expected '456'" run_verify
write_acceptance "$BUILD_NUMBER" blocked
expect_failure acceptance-nonpassing-test "manual test 'install' must have status 'pass'" run_verify
write_acceptance

LAUNCH_TEST_EVIDENCE_BODY_OVERRIDE='tampered launch evidence' \
  expect_failure evidence-content-mismatch 'content hash mismatch' run_verify

LAUNCH_TEST_EVIDENCE_EMPTY=1 \
  expect_failure empty-evidence 'launch evidence record manual-install-1 is empty' run_verify

LAUNCH_TEST_READY_ENVIRONMENT_OVERRIDE=staging \
  expect_failure ready-environment "/ready environment mismatch: expected 'production'" run_verify

LAUNCH_TEST_WORKER_RELEASES_MATCH_OVERRIDE=false \
  expect_failure worker-release-mismatch 'fresh worker releases do not all match the API release' run_verify

LAUNCH_TEST_QUEUE_DEPTH_OVERRIDE='{"critical":6}' \
  expect_failure queue-depth 'exceeds MAX_QUEUE_DEPTH_PER_QUEUE=5' run_verify

LAUNCH_TEST_OLDEST_QUEUED_AGE_OVERRIDE=31 \
  expect_failure queue-age 'exceeds MAX_OLDEST_QUEUED_AGE_SECONDS=30' run_verify

LAUNCH_TEST_MAX_AGE=3601 \
  expect_failure unbounded-threshold 'cannot exceed the production safety cap of 3600' run_verify

printf '%s\n' 'tamper' >> "$DMG_PATH"
expect_failure checksum-tamper 'SHA256SUMS verification failed' run_verify
printf '%s\n' 'deterministic fake DMG' > "$DMG_PATH"
write_checksums

write_metadata 9.9.9
write_checksums
expect_failure metadata-version 'release metadata version mismatch' run_verify
write_metadata "$VERSION"
write_checksums

write_metadata "$VERSION" 99Z999
write_checksums
expect_failure metadata-xcode-build "release metadata xcode_build mismatch: expected '16F6'" run_verify
write_metadata "$VERSION"
write_checksums

LAUNCH_TEST_DMG_APP_OVERRIDE="$MISMATCHED_DMG_APP_PATH" \
  expect_failure mounted-app-version 'version mismatch: expected 1.2.3, found 9.9.9' run_verify

LAUNCH_TEST_APP_PATH="$MISMATCHED_COMMIT_APP_PATH" \
  expect_failure supplied-app-commit 'source-commit mismatch: expected 0123456789abcdef0123456789abcdef01234567, found aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' run_verify

LAUNCH_TEST_DOWNLOAD_BODY_OVERRIDE='stale public DMG' \
  expect_failure public-download-mismatch 'public macOS download does not match the approved DMG' run_verify

run_verify > "$TEST_DIR/production-success.stdout"
grep -Fq 'Production launch verification passed' "$TEST_DIR/production-success.stdout" \
  || fail "complete production verification did not report success"

LAUNCH_TEST_MODE=non-production \
LAUNCH_TEST_BACKEND_URL=http://localhost:3001 \
LAUNCH_TEST_EXPECTED_RELEASE_SHA='' \
LAUNCH_TEST_EXPECTED_VERSION='' \
LAUNCH_TEST_EXPECTED_BUILD_NUMBER='' \
LAUNCH_TEST_TEAM='' \
LAUNCH_TEST_APP_PATH='' \
LAUNCH_TEST_DMG_PATH='' \
LAUNCH_TEST_METADATA_PATH='' \
LAUNCH_TEST_CHECKSUM_PATH='' \
LAUNCH_TEST_OPS_TOKEN='' \
LAUNCH_TEST_WEB_URL='' \
LAUNCH_TEST_SKIP_OPS=1 \
LAUNCH_TEST_SKIP_ARTIFACT=1 \
LAUNCH_TEST_SKIP_PUBLIC=1 \
LAUNCH_TEST_SKIP_ACCEPTANCE=1 \
LAUNCH_TEST_ALLOW_INSECURE=1 \
LAUNCH_TEST_READY_ENVIRONMENT_OVERRIDE=staging \
run_verify > "$TEST_DIR/non-production-success.stdout"
grep -Fq 'skipped gates are not production approval' "$TEST_DIR/non-production-success.stdout" \
  || fail "non-production override run did not report its limited scope"

echo "verify-launch fail-closed exact-DMG acceptance, release, queue, checksum, metadata, and mounted-DMG tests passed"
