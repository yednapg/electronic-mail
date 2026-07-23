#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCH_VERIFY_MODE="${LAUNCH_VERIFY_MODE:-production}"
BACKEND_URL="${BACKEND_URL:-}"
APP_PATH="${APP_PATH:-}"
DMG_PATH="${DMG_PATH:-}"
OPS_BEARER_TOKEN="${OPS_BEARER_TOKEN:-}"
WEB_URL="${WEB_URL:-}"
APPLE_DEVELOPMENT_TEAM="${APPLE_DEVELOPMENT_TEAM:-}"
EXPECTED_RELEASE_SHA="${EXPECTED_RELEASE_SHA:-}"
EXPECTED_VERSION="${EXPECTED_VERSION:-}"
EXPECTED_BUILD_NUMBER="${EXPECTED_BUILD_NUMBER:-}"
RELEASE_METADATA_PATH="${RELEASE_METADATA_PATH:-}"
SHA256SUMS_PATH="${SHA256SUMS_PATH:-}"
LAUNCH_ACCEPTANCE_PATH="${LAUNCH_ACCEPTANCE_PATH:-}"
EVIDENCE_ORIGIN="${EVIDENCE_ORIGIN:-}"
EVIDENCE_BEARER_TOKEN="${EVIDENCE_BEARER_TOKEN:-}"
SKIP_OPS_HEALTH="${SKIP_OPS_HEALTH:-0}"
SKIP_ARTIFACT_VERIFY="${SKIP_ARTIFACT_VERIFY:-0}"
SKIP_PUBLIC_PAGE_VERIFY="${SKIP_PUBLIC_PAGE_VERIFY:-0}"
SKIP_ACCEPTANCE_VERIFY="${SKIP_ACCEPTANCE_VERIFY:-0}"
ALLOW_INSECURE_LAUNCH_VERIFY="${ALLOW_INSECURE_LAUNCH_VERIFY:-0}"

# These may be made stricter for a launch, but the hard caps prevent an
# override from silently turning the backlog gates into no-ops.
MAX_QUEUE_DEPTH_PER_QUEUE="${MAX_QUEUE_DEPTH_PER_QUEUE:-1000}"
MAX_TOTAL_QUEUE_DEPTH="${MAX_TOTAL_QUEUE_DEPTH:-2500}"
MAX_OLDEST_QUEUED_AGE_SECONDS="${MAX_OLDEST_QUEUED_AGE_SECONDS:-900}"
HARD_MAX_QUEUE_DEPTH_PER_QUEUE=10000
HARD_MAX_TOTAL_QUEUE_DEPTH=25000
HARD_MAX_OLDEST_QUEUED_AGE_SECONDS=3600
EXPECTED_EVIDENCE_RECORD_COUNT=20

TMP_DIR=""
DMG_MOUNT_POINT=""
DMG_IS_MOUNTED=0
VERIFIED_DMG_SHA256=""
RELEASE_CREATED_AT_UTC=""

fail() {
  echo "launch verification failed: $*" >&2
  exit 1
}

cleanup() {
  local status=$?
  trap - EXIT
  set +e
  if [ "$DMG_IS_MOUNTED" = "1" ] && [ -n "$DMG_MOUNT_POINT" ]; then
    hdiutil detach "$DMG_MOUNT_POINT" >/dev/null 2>&1
  fi
  if [ -n "$TMP_DIR" ]; then
    rm -rf "$TMP_DIR"
  fi
  exit "$status"
}

require_value() {
  local name="$1"
  local value="$2"
  [ -n "$value" ] || fail "$name is required for production launch verification"
}

validate_switch() {
  local name="$1"
  local value="$2"
  case "$value" in
    0 | 1) ;;
    *) fail "$name must be 0 or 1" ;;
  esac
}

validate_threshold() {
  local name="$1"
  local value="$2"
  local hard_max="$3"
  [[ "$value" =~ ^(0|[1-9][0-9]*)$ ]] || fail "$name must be a non-negative integer"
  [ "$value" -le "$hard_max" ] || fail "$name cannot exceed the production safety cap of $hard_max"
}

case "$LAUNCH_VERIFY_MODE" in
  production | non-production) ;;
  *) fail "LAUNCH_VERIFY_MODE must be production or non-production" ;;
esac
validate_switch SKIP_OPS_HEALTH "$SKIP_OPS_HEALTH"
validate_switch SKIP_ARTIFACT_VERIFY "$SKIP_ARTIFACT_VERIFY"
validate_switch SKIP_PUBLIC_PAGE_VERIFY "$SKIP_PUBLIC_PAGE_VERIFY"
validate_switch SKIP_ACCEPTANCE_VERIFY "$SKIP_ACCEPTANCE_VERIFY"
validate_switch ALLOW_INSECURE_LAUNCH_VERIFY "$ALLOW_INSECURE_LAUNCH_VERIFY"

[ -n "$BACKEND_URL" ] || fail "set BACKEND_URL to the deployed API origin"

if [ "$LAUNCH_VERIFY_MODE" = "production" ]; then
  [ "$SKIP_OPS_HEALTH" = "0" ] || fail "SKIP_OPS_HEALTH is only allowed with LAUNCH_VERIFY_MODE=non-production"
  [ "$SKIP_ARTIFACT_VERIFY" = "0" ] || fail "SKIP_ARTIFACT_VERIFY is only allowed with LAUNCH_VERIFY_MODE=non-production"
  [ "$SKIP_PUBLIC_PAGE_VERIFY" = "0" ] || fail "SKIP_PUBLIC_PAGE_VERIFY is only allowed with LAUNCH_VERIFY_MODE=non-production"
  [ "$SKIP_ACCEPTANCE_VERIFY" = "0" ] || fail "SKIP_ACCEPTANCE_VERIFY is only allowed with LAUNCH_VERIFY_MODE=non-production"
  [ "$ALLOW_INSECURE_LAUNCH_VERIFY" = "0" ] || fail "ALLOW_INSECURE_LAUNCH_VERIFY is only allowed with LAUNCH_VERIFY_MODE=non-production"

  require_value WEB_URL "$WEB_URL"
  require_value OPS_BEARER_TOKEN "$OPS_BEARER_TOKEN"
  require_value APPLE_DEVELOPMENT_TEAM "$APPLE_DEVELOPMENT_TEAM"
  require_value APP_PATH "$APP_PATH"
  require_value DMG_PATH "$DMG_PATH"
  require_value EXPECTED_RELEASE_SHA "$EXPECTED_RELEASE_SHA"
  require_value EXPECTED_VERSION "$EXPECTED_VERSION"
  require_value EXPECTED_BUILD_NUMBER "$EXPECTED_BUILD_NUMBER"
  require_value RELEASE_METADATA_PATH "$RELEASE_METADATA_PATH"
  require_value SHA256SUMS_PATH "$SHA256SUMS_PATH"
  require_value LAUNCH_ACCEPTANCE_PATH "$LAUNCH_ACCEPTANCE_PATH"
  require_value EVIDENCE_ORIGIN "$EVIDENCE_ORIGIN"
  require_value EVIDENCE_BEARER_TOKEN "$EVIDENCE_BEARER_TOKEN"
fi

if [ "$SKIP_OPS_HEALTH" = "0" ]; then
  [ -n "$OPS_BEARER_TOKEN" ] || fail "OPS_BEARER_TOKEN is required unless the non-production ops-health override is enabled"
  [ -n "$EXPECTED_RELEASE_SHA" ] || fail "EXPECTED_RELEASE_SHA is required when worker health is verified"
  validate_threshold MAX_QUEUE_DEPTH_PER_QUEUE "$MAX_QUEUE_DEPTH_PER_QUEUE" "$HARD_MAX_QUEUE_DEPTH_PER_QUEUE"
  validate_threshold MAX_TOTAL_QUEUE_DEPTH "$MAX_TOTAL_QUEUE_DEPTH" "$HARD_MAX_TOTAL_QUEUE_DEPTH"
  validate_threshold MAX_OLDEST_QUEUED_AGE_SECONDS "$MAX_OLDEST_QUEUED_AGE_SECONDS" "$HARD_MAX_OLDEST_QUEUED_AGE_SECONDS"
fi

if [ "$SKIP_ARTIFACT_VERIFY" = "0" ]; then
  [ -n "$APP_PATH" ] || fail "APP_PATH is required unless the non-production artifact override is enabled"
  [ -n "$DMG_PATH" ] || fail "DMG_PATH is required unless the non-production artifact override is enabled"
  [ -n "$APPLE_DEVELOPMENT_TEAM" ] || fail "APPLE_DEVELOPMENT_TEAM is required unless the non-production artifact override is enabled"
  [ -n "$EXPECTED_VERSION" ] || fail "EXPECTED_VERSION is required when artifacts are verified"
  [ -n "$EXPECTED_BUILD_NUMBER" ] || fail "EXPECTED_BUILD_NUMBER is required when artifacts are verified"
  [ -n "$EXPECTED_RELEASE_SHA" ] || fail "EXPECTED_RELEASE_SHA is required when artifacts are verified"
  [ -n "$RELEASE_METADATA_PATH" ] || fail "RELEASE_METADATA_PATH is required when artifacts are verified"
  [ -n "$SHA256SUMS_PATH" ] || fail "SHA256SUMS_PATH is required when artifacts are verified"
fi

if [ "$SKIP_PUBLIC_PAGE_VERIFY" = "0" ]; then
  [ -n "$WEB_URL" ] || fail "WEB_URL is required unless the non-production public-page override is enabled"
fi

if [ "$SKIP_ACCEPTANCE_VERIFY" = "0" ]; then
  [ "$SKIP_ARTIFACT_VERIFY" = "0" ] || fail "acceptance verification requires artifact verification"
  [ -n "$LAUNCH_ACCEPTANCE_PATH" ] || fail "LAUNCH_ACCEPTANCE_PATH is required unless the non-production acceptance override is enabled"
  [ -n "$EXPECTED_RELEASE_SHA" ] || fail "EXPECTED_RELEASE_SHA is required when launch acceptance is verified"
  [ -n "$EXPECTED_VERSION" ] || fail "EXPECTED_VERSION is required when launch acceptance is verified"
  [ -n "$EXPECTED_BUILD_NUMBER" ] || fail "EXPECTED_BUILD_NUMBER is required when launch acceptance is verified"
  [ -n "$WEB_URL" ] || fail "WEB_URL is required when launch acceptance is verified"
  [ -n "$EVIDENCE_ORIGIN" ] || fail "EVIDENCE_ORIGIN is required when launch acceptance is verified"
  [ -n "$EVIDENCE_BEARER_TOKEN" ] || fail "EVIDENCE_BEARER_TOKEN is required when launch acceptance is verified"
  [[ ! "$EVIDENCE_BEARER_TOKEN" =~ [[:cntrl:]] ]] || fail "EVIDENCE_BEARER_TOKEN cannot contain control characters"
fi

if [ -n "$EXPECTED_RELEASE_SHA" ]; then
  [[ "$EXPECTED_RELEASE_SHA" =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]] || \
    fail "EXPECTED_RELEASE_SHA must be a full lowercase 40- or 64-character Git commit SHA"
fi
if [ -n "$EXPECTED_VERSION" ]; then
  [[ "$EXPECTED_VERSION" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || \
    fail "EXPECTED_VERSION must contain two or three numeric components"
fi
if [ -n "$EXPECTED_BUILD_NUMBER" ]; then
  [[ "$EXPECTED_BUILD_NUMBER" =~ ^[1-9][0-9]*$ ]] || \
    fail "EXPECTED_BUILD_NUMBER must be a positive integer"
fi
if [ -n "$APPLE_DEVELOPMENT_TEAM" ]; then
  [[ "$APPLE_DEVELOPMENT_TEAM" =~ ^[A-Z0-9]{10}$ ]] || \
    fail "APPLE_DEVELOPMENT_TEAM must be the 10-character Apple team identifier"
fi

[ -x "$ROOT_DIR/.venv/bin/python" ] || fail "canonical root .venv is missing; run npm run setup"
if grep -R "cd backend && \.venv/bin" "$ROOT_DIR/package.json" "$ROOT_DIR/scripts" >/dev/null 2>&1; then
  fail "a runtime script still depends on the ignored backend/.venv path"
fi
PYTHONPATH="$ROOT_DIR/backend" "$ROOT_DIR/.venv/bin/python" -c "import app.core.config, app.main"

BACKEND_URL="${BACKEND_URL%/}"
WEB_URL="${WEB_URL%/}"
if [ "$ALLOW_INSECURE_LAUNCH_VERIFY" != "1" ]; then
  [[ "$BACKEND_URL" == https://* ]] || fail "BACKEND_URL must use HTTPS"
fi
if [ "$LAUNCH_VERIFY_MODE" = "production" ]; then
  python3 - "$BACKEND_URL" "$WEB_URL" <<'PY'
import ipaddress
import sys
from urllib.parse import urlsplit

def validate_origin(label, value):
    if value != value.strip() or any(ord(character) < 33 for character in value):
        raise SystemExit(f"launch verification failed: {label} cannot contain whitespace or control characters")
    url = urlsplit(value)
    if url.scheme != "https" or not url.hostname:
        raise SystemExit(f"launch verification failed: {label} must be a production HTTPS origin")
    if url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
        raise SystemExit(
            f"launch verification failed: {label} must be an origin without credentials, path, query, or fragment"
        )
    try:
        url.port
    except ValueError as error:
        raise SystemExit(f"launch verification failed: {label} has an invalid port: {error}")
    host = url.hostname.lower().rstrip(".")
    reserved_suffixes = (".invalid", ".test", ".example", ".localhost", ".local")
    reserved_hosts = {"example.com", "example.net", "example.org"}
    if (
        host == "localhost"
        or host in reserved_hosts
        or host.endswith(reserved_suffixes)
        or any(host.endswith(f".{reserved}") for reserved in reserved_hosts)
    ):
        raise SystemExit(f"launch verification failed: {label} cannot use a local, reserved, or placeholder hostname")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise SystemExit(f"launch verification failed: {label} cannot use a private, loopback, or link-local IP address")
    if address is None and "." not in host:
        raise SystemExit(f"launch verification failed: {label} must use a fully qualified public hostname")

validate_origin("BACKEND_URL", sys.argv[1])
validate_origin("WEB_URL", sys.argv[2])
PY
fi

TMP_DIR="$(mktemp -d)"
trap cleanup EXIT

request_json() {
  local path="$1"
  local output="$2"
  shift 2
  curl --silent --show-error --fail-with-body --retry 3 --retry-all-errors \
    --connect-timeout 10 --max-time 30 \
    --dump-header "$output.headers" \
    "$@" "$BACKEND_URL$path" > "$output"
}

echo "==> Checking liveness and exact release"
request_json /health "$TMP_DIR/health.json"
python3 - "$TMP_DIR/health.json" "$EXPECTED_RELEASE_SHA" <<'PY'
import json
import sys

path, expected_release = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
if not isinstance(payload, dict):
    raise SystemExit("/health response must be a JSON object")
if payload.get("status") != "ok":
    raise SystemExit("/health status is not ok")
release = payload.get("release")
if expected_release:
    if release != expected_release:
        raise SystemExit(f"/health release mismatch: expected {expected_release}, found {release!r}")
elif release in (None, "", "local"):
    raise SystemExit("/health did not identify a deployed release")
PY
grep -qi '^x-request-id:' "$TMP_DIR/health.json.headers" || fail "X-Request-ID response header is missing"
grep -qi '^x-content-type-options: nosniff' "$TMP_DIR/health.json.headers" || fail "security headers are missing"
if [[ "$BACKEND_URL" == https://* ]]; then
  grep -qi '^strict-transport-security:' "$TMP_DIR/health.json.headers" || fail "HSTS is missing"
fi

echo "==> Checking database/config readiness and exact release"
request_json /ready "$TMP_DIR/ready.json"
python3 - "$TMP_DIR/ready.json" "$EXPECTED_RELEASE_SHA" "$LAUNCH_VERIFY_MODE" <<'PY'
import json
import sys

path, expected_release, mode = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
if not isinstance(payload, dict):
    raise SystemExit("/ready response must be a JSON object")
checks = (
    (payload.get("status") == "ready", "/ready status is not ready"),
    (payload.get("database") == "postgres", "/ready database is not postgres"),
    (payload.get("google_configured") is True, "/ready reports Google is not configured"),
    (payload.get("ai_enabled") is False, "/ready reports AI is enabled"),
    (
        isinstance(payload.get("schema_revision"), str)
        and payload.get("schema_revision")
        and payload.get("schema_revision") == payload.get("schema_head"),
        "/ready schema revision does not exactly match its head",
    ),
)
for condition, message in checks:
    if not condition:
        raise SystemExit(message)
release = payload.get("release")
if expected_release:
    if release != expected_release:
        raise SystemExit(f"/ready release mismatch: expected {expected_release}, found {release!r}")
elif release in (None, "", "local"):
    raise SystemExit("/ready did not identify a deployed release")
if mode == "production" and payload.get("environment") != "production":
    raise SystemExit(f"/ready environment mismatch: expected 'production', found {payload.get('environment')!r}")
PY

if [ "$SKIP_OPS_HEALTH" = "0" ]; then
  echo "==> Checking worker release parity and bounded queue health"
  request_json /v1/ops/health "$TMP_DIR/ops.json" -H "Authorization: Bearer $OPS_BEARER_TOKEN"
  python3 - \
    "$TMP_DIR/ops.json" \
    "$EXPECTED_RELEASE_SHA" \
    "$LAUNCH_VERIFY_MODE" \
    "$MAX_QUEUE_DEPTH_PER_QUEUE" \
    "$MAX_TOTAL_QUEUE_DEPTH" \
    "$MAX_OLDEST_QUEUED_AGE_SECONDS" <<'PY'
import json
import sys

(
    path,
    expected_release,
    mode,
    max_per_queue_text,
    max_total_text,
    max_oldest_text,
) = sys.argv[1:]
max_per_queue = int(max_per_queue_text)
max_total = int(max_total_text)
max_oldest = int(max_oldest_text)
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
if not isinstance(payload, dict):
    raise SystemExit("ops health response must be a JSON object")
if payload.get("release") != expected_release:
    raise SystemExit(f"ops release mismatch: expected {expected_release}, found {payload.get('release')!r}")
if mode == "production" and payload.get("environment") != "production":
    raise SystemExit(f"ops environment mismatch: expected 'production', found {payload.get('environment')!r}")
if payload.get("worker_online") is not True:
    raise SystemExit("ops health reports no fresh worker")
if payload.get("required_queues_ready") is not True:
    raise SystemExit("ops health reports required queues are not ready")
if payload.get("worker_releases_match") is not True:
    raise SystemExit("ops health reports fresh worker releases do not all match the API release")

for field in ("dead_jobs", "stale_running_jobs"):
    value = payload.get(field)
    if type(value) is not int or value != 0:
        raise SystemExit(f"ops health requires {field}=0, found {value!r}")

depths = payload.get("queue_depth")
if not isinstance(depths, dict):
    raise SystemExit("ops queue_depth must be an object")
total = 0
for queue, depth in depths.items():
    if not isinstance(queue, str) or not queue:
        raise SystemExit("ops queue_depth contains an invalid queue name")
    if type(depth) is not int or depth < 0:
        raise SystemExit(f"ops queue {queue!r} has invalid depth {depth!r}")
    if depth > max_per_queue:
        raise SystemExit(f"ops queue {queue!r} depth {depth} exceeds MAX_QUEUE_DEPTH_PER_QUEUE={max_per_queue}")
    total += depth
if total > max_total:
    raise SystemExit(f"ops total queue depth {total} exceeds MAX_TOTAL_QUEUE_DEPTH={max_total}")

oldest = payload.get("oldest_queued_age_seconds")
if oldest is not None and (type(oldest) is not int or oldest < 0):
    raise SystemExit(f"ops oldest_queued_age_seconds is invalid: {oldest!r}")
if total == 0 and oldest is not None:
    raise SystemExit("ops reports an oldest queued age while queue_depth is empty")
if total > 0 and oldest is None:
    raise SystemExit("ops omitted oldest_queued_age_seconds while jobs are queued")
if oldest is not None and oldest > max_oldest:
    raise SystemExit(
        f"ops oldest queued job age {oldest}s exceeds MAX_OLDEST_QUEUED_AGE_SECONDS={max_oldest}"
    )
PY
else
  echo "==> Non-production override: authenticated worker-health check skipped"
fi

verify_checksum_manifest() {
  local checksum_dir
  local checksum_name

  [ -s "$SHA256SUMS_PATH" ] || fail "SHA256SUMS_PATH does not point to a non-empty checksum manifest"
  [ ! -L "$SHA256SUMS_PATH" ] || fail "SHA256SUMS_PATH must not be a symbolic link"
  [ -f "$DMG_PATH" ] || fail "DMG_PATH does not exist"
  [ -s "$RELEASE_METADATA_PATH" ] || fail "RELEASE_METADATA_PATH does not point to non-empty metadata"

  checksum_dir="$(cd "$(dirname "$SHA256SUMS_PATH")" && pwd -P)"
  checksum_name="$(basename "$SHA256SUMS_PATH")"
  [[ "$checksum_name" != -* ]] || fail "SHA256SUMS_PATH filename cannot begin with a hyphen"

  python3 - "$checksum_dir/$checksum_name" "$DMG_PATH" "$RELEASE_METADATA_PATH" <<'PY'
import os
import re
import sys

checksum_path, dmg_path, metadata_path = map(os.path.realpath, sys.argv[1:])
checksum_dir = os.path.dirname(checksum_path)

def require(condition, message):
    if not condition:
        raise SystemExit(message)

require(os.path.dirname(dmg_path) == checksum_dir, "DMG_PATH must be in the SHA256SUMS directory")
require(os.path.dirname(metadata_path) == checksum_dir, "RELEASE_METADATA_PATH must be in the SHA256SUMS directory")

with open(checksum_path, encoding="utf-8") as handle:
    lines = handle.read().splitlines()
require(lines, "SHA256SUMS is empty")
entries = set()
for line_number, line in enumerate(lines, 1):
    match = re.fullmatch(r"([0-9A-Fa-f]{64}) ([ *])(.+)", line)
    require(match is not None, f"SHA256SUMS line {line_number} is malformed")
    filename = match.group(3)
    require(filename not in entries, f"SHA256SUMS contains duplicate entry {filename!r}")
    require(
        filename == os.path.basename(filename) and "/" not in filename and "\\" not in filename,
        f"SHA256SUMS entry must be a filename in the artifact directory: {filename!r}",
    )
    require(os.path.isfile(os.path.join(checksum_dir, filename)), f"SHA256SUMS entry is missing: {filename}")
    entries.add(filename)

for required_path, label in ((dmg_path, "DMG_PATH"), (metadata_path, "RELEASE_METADATA_PATH")):
    filename = os.path.basename(required_path)
    require(filename in entries, f"SHA256SUMS does not cover {label}: {filename}")
PY

  (
    cd "$checksum_dir"
    shasum -a 256 -c "$checksum_name"
  ) || fail "SHA256SUMS verification failed"
}

verify_release_metadata() {
  python3 - \
    "$RELEASE_METADATA_PATH" \
    "$EXPECTED_RELEASE_SHA" \
    "$EXPECTED_VERSION" \
    "$EXPECTED_BUILD_NUMBER" \
    "$BACKEND_URL" \
    "$APPLE_DEVELOPMENT_TEAM" <<'PY'
from datetime import datetime, timedelta, timezone
import json
import sys

path, release, version, build, backend, team = sys.argv[1:]

def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate metadata field {key!r}")
        result[key] = value
    return result

try:
    with open(path, encoding="utf-8") as handle:
        metadata = json.load(handle, object_pairs_hook=reject_duplicates)
except (OSError, ValueError, json.JSONDecodeError) as error:
    raise SystemExit(f"release metadata is invalid: {error}")
if not isinstance(metadata, dict):
    raise SystemExit("release metadata must be a JSON object")

expected = {
    "product": "Electronic Mail",
    "bundle_identifier": "app.electronicmail.mac",
    "source_commit": release,
    "version": version,
    "build_number": build,
    "backend_origin": backend,
    "developer_team": team,
}
for field, expected_value in expected.items():
    if metadata.get(field) != expected_value:
        raise SystemExit(
            f"release metadata {field} mismatch: expected {expected_value!r}, found {metadata.get(field)!r}"
        )
if metadata.get("notarized") is not True:
    raise SystemExit("release metadata must record notarized=true")
if metadata.get("source_tree_clean") is not True:
    raise SystemExit("release metadata must record source_tree_clean=true")
created_at = metadata.get("created_at_utc")
if not isinstance(created_at, str):
    raise SystemExit("release metadata created_at_utc must be an ISO-8601 timestamp")
normalized = created_at[:-1] + "+00:00" if created_at.endswith("Z") else created_at
try:
    parsed = datetime.fromisoformat(normalized)
except ValueError as error:
    raise SystemExit(f"release metadata created_at_utc is invalid: {error}")
if parsed.tzinfo is None or parsed.utcoffset() is None:
    raise SystemExit("release metadata created_at_utc must include a time-zone offset")
parsed = parsed.astimezone(timezone.utc)
if parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
    raise SystemExit("release metadata created_at_utc cannot be in the future")
print(parsed.isoformat().replace("+00:00", "Z"))
PY
}

verify_app_bundle() {
  local app_path="$1"
  local label="$2"

  [ -d "$app_path" ] || fail "$label does not point to an app bundle"
  echo "==> Deeply verifying $label"
  APP_PATH="$app_path" \
  EXPECTED_BACKEND_URL="$BACKEND_URL" \
  EXPECTED_VERSION="$EXPECTED_VERSION" \
  EXPECTED_BUILD_NUMBER="$EXPECTED_BUILD_NUMBER" \
  EXPECTED_SOURCE_COMMIT="$EXPECTED_RELEASE_SHA" \
  EXPECTED_TEAM_ID="$APPLE_DEVELOPMENT_TEAM" \
  SIGNING_MODE=developer-id \
  REQUIRE_NOTARIZATION=1 \
  bash "$ROOT_DIR/scripts/verify-macos-release.sh"
}

if [ "$SKIP_ARTIFACT_VERIFY" = "0" ]; then
  echo "==> Authenticating release checksums and metadata"
  verify_checksum_manifest
  VERIFIED_DMG_SHA256="$(shasum -a 256 "$DMG_PATH" | awk '{print $1}')"
  RELEASE_CREATED_AT_UTC="$(verify_release_metadata)"
  verify_app_bundle "$APP_PATH" "the supplied APP_PATH"

  echo "==> Verifying the signed, notarized DMG container"
  hdiutil verify "$DMG_PATH"
  codesign --verify --verbose=2 "$DMG_PATH"
  DMG_SIGNATURE_DETAILS="$(codesign -dvvv "$DMG_PATH" 2>&1)"
  printf '%s\n' "$DMG_SIGNATURE_DETAILS" | grep -q '^Authority=Developer ID Application:' || \
    fail "DMG is not signed with a Developer ID Application certificate"
  DMG_TEAM_ID="$(printf '%s\n' "$DMG_SIGNATURE_DETAILS" | sed -n 's/^TeamIdentifier=//p' | head -1)"
  [ "$DMG_TEAM_ID" = "$APPLE_DEVELOPMENT_TEAM" ] || \
    fail "DMG signature team mismatch: expected $APPLE_DEVELOPMENT_TEAM, found ${DMG_TEAM_ID:-none}"
  xcrun stapler validate "$DMG_PATH"
  spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG_PATH"

  echo "==> Mounting the supplied DMG and verifying its actual app"
  DMG_MOUNT_POINT="$TMP_DIR/dmg-mount"
  mkdir -p "$DMG_MOUNT_POINT"
  hdiutil attach -readonly -nobrowse -mountpoint "$DMG_MOUNT_POINT" "$DMG_PATH" >/dev/null
  DMG_IS_MOUNTED=1
  [ -L "$DMG_MOUNT_POINT/Applications" ] || fail "DMG is missing the Applications shortcut"
  [ "$(readlink "$DMG_MOUNT_POINT/Applications")" = "/Applications" ] || \
    fail "DMG Applications shortcut does not target /Applications"
  DMG_APP_PATH="$DMG_MOUNT_POINT/ElectronicMail.app"
  [ ! -L "$DMG_APP_PATH" ] || fail "DMG ElectronicMail.app must not be a symbolic link"
  verify_app_bundle "$DMG_APP_PATH" "ElectronicMail.app mounted from DMG_PATH"
  hdiutil detach "$DMG_MOUNT_POINT" >/dev/null || fail "could not detach the verified DMG"
  DMG_IS_MOUNTED=0
else
  echo "==> Non-production override: signed/notarized artifact verification skipped"
fi

if [ "$SKIP_ACCEPTANCE_VERIFY" = "0" ]; then
  [ -n "$VERIFIED_DMG_SHA256" ] || fail "approved DMG SHA-256 is unavailable for launch acceptance verification"
  echo "==> Verifying exact-DMG-bound manual acceptance and launch approvals"
  EVIDENCE_INDEX_PATH="$TMP_DIR/launch-evidence.tsv"
  python3 "$ROOT_DIR/scripts/verify_launch_acceptance.py" \
    --manifest "$LAUNCH_ACCEPTANCE_PATH" \
    --release-sha "$EXPECTED_RELEASE_SHA" \
    --dmg-sha256 "$VERIFIED_DMG_SHA256" \
    --release-created-at "$RELEASE_CREATED_AT_UTC" \
    --evidence-origin "$EVIDENCE_ORIGIN" \
    --version "$EXPECTED_VERSION" \
    --build-number "$EXPECTED_BUILD_NUMBER" \
    --backend-origin "$BACKEND_URL" \
    --web-origin "$WEB_URL" \
    --evidence-index "$EVIDENCE_INDEX_PATH"

  echo "==> Downloading and hashing every immutable launch-evidence record"
  evidence_count=0
  while IFS=$'\t' read -r evidence_id evidence_url expected_evidence_sha; do
    [ -n "$evidence_id" ] && [ -n "$evidence_url" ] && [ -n "$expected_evidence_sha" ] || \
      fail "launch evidence index contains a malformed row"
    evidence_count=$((evidence_count + 1))
    [ "$evidence_count" -le "$EXPECTED_EVIDENCE_RECORD_COUNT" ] || \
      fail "launch evidence index contains more than $EXPECTED_EVIDENCE_RECORD_COUNT records"
    evidence_path="$TMP_DIR/evidence-$evidence_count.bin"
    evidence_status="$(
      curl --silent --show-error --proto '=https' --tlsv1.2 \
        --connect-timeout 10 --max-time 60 --max-filesize 268435456 \
        --output "$evidence_path" --write-out '%{http_code}' \
        -H "Authorization: Bearer $EVIDENCE_BEARER_TOKEN" \
        "$evidence_url"
    )" || fail "could not download launch evidence record $evidence_id"
    [ "$evidence_status" = "200" ] || fail "launch evidence record $evidence_id returned HTTP $evidence_status instead of 200"
    [ -s "$evidence_path" ] || fail "launch evidence record $evidence_id is empty"
    actual_evidence_sha="$(shasum -a 256 "$evidence_path" | awk '{print $1}')"
    rm -f "$evidence_path"
    [ "$actual_evidence_sha" = "$expected_evidence_sha" ] || \
      fail "launch evidence record $evidence_id content hash mismatch"
  done < "$EVIDENCE_INDEX_PATH"
  [ "$evidence_count" -eq "$EXPECTED_EVIDENCE_RECORD_COUNT" ] || \
    fail "launch evidence index must contain exactly $EXPECTED_EVIDENCE_RECORD_COUNT records"
else
  echo "==> Non-production override: manual acceptance and accountable approvals skipped"
fi

if [ "$SKIP_PUBLIC_PAGE_VERIFY" = "0" ]; then
  WEB_URL="$WEB_URL" \
  EXPECTED_DMG_SHA256="$VERIFIED_DMG_SHA256" \
    bash "$ROOT_DIR/scripts/verify-public-pages.sh"
else
  echo "==> Non-production override: public privacy/terms/support verification skipped"
fi

if [ "$LAUNCH_VERIFY_MODE" = "production" ]; then
  echo "==> Production launch verification passed for release $EXPECTED_RELEASE_SHA, version $EXPECTED_VERSION ($EXPECTED_BUILD_NUMBER)"
else
  echo "==> Non-production launch verification passed; skipped gates are not production approval"
fi
