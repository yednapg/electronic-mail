#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=macos-release-toolchain.env
source "$ROOT_DIR/scripts/macos-release-toolchain.env"

fail() {
  echo "macOS release toolchain verification failed: $*" >&2
  exit 1
}

[[ "$APPROVED_XCODE_VERSION" =~ ^[0-9]+\.[0-9]+([.][0-9]+)?$ ]] || \
  fail "APPROVED_XCODE_VERSION is invalid"
[[ "$APPROVED_XCODE_BUILD" =~ ^[0-9A-Za-z]+$ ]] || \
  fail "APPROVED_XCODE_BUILD is invalid"
[[ "$APPROVED_DEVELOPER_DIR" == /Applications/Xcode_*.app/Contents/Developer ]] || \
  fail "APPROVED_DEVELOPER_DIR must identify a versioned Xcode application"
case "$APPROVED_DEVELOPER_DIR" in
  *[Bb]eta*) fail "beta Xcode cannot be an approved release toolchain" ;;
esac

DEVELOPER_DIR="${DEVELOPER_DIR:-$APPROVED_DEVELOPER_DIR}"
EXPECTED_XCODE_VERSION="${EXPECTED_XCODE_VERSION:-$APPROVED_XCODE_VERSION}"
EXPECTED_XCODE_BUILD="${EXPECTED_XCODE_BUILD:-$APPROVED_XCODE_BUILD}"

case "$DEVELOPER_DIR" in
  *[Bb]eta*) fail "stable Xcode is required; beta Xcode is forbidden for release work" ;;
esac
[ "$DEVELOPER_DIR" = "$APPROVED_DEVELOPER_DIR" ] || \
  fail "DEVELOPER_DIR must be $APPROVED_DEVELOPER_DIR"
[ "$EXPECTED_XCODE_VERSION" = "$APPROVED_XCODE_VERSION" ] || \
  fail "EXPECTED_XCODE_VERSION must be $APPROVED_XCODE_VERSION"
[ "$EXPECTED_XCODE_BUILD" = "$APPROVED_XCODE_BUILD" ] || \
  fail "EXPECTED_XCODE_BUILD must be $APPROVED_XCODE_BUILD"
[ -x "$DEVELOPER_DIR/usr/bin/xcodebuild" ] || \
  fail "approved xcodebuild is missing at $DEVELOPER_DIR/usr/bin/xcodebuild"

xcode_output="$("$DEVELOPER_DIR/usr/bin/xcodebuild" -version)" || \
  fail "approved xcodebuild could not report its version"
actual_version="$(printf '%s\n' "$xcode_output" | sed -n '1s/^Xcode //p')"
actual_build="$(printf '%s\n' "$xcode_output" | sed -n '2s/^Build version //p')"
[ "$actual_version" = "$APPROVED_XCODE_VERSION" ] || \
  fail "Xcode version mismatch: expected $APPROVED_XCODE_VERSION, found ${actual_version:-unknown}"
[ "$actual_build" = "$APPROVED_XCODE_BUILD" ] || \
  fail "Xcode build mismatch: expected $APPROVED_XCODE_BUILD, found ${actual_build:-unknown}"

printf 'Approved macOS release toolchain verified: Xcode %s (%s) at %s\n' \
  "$actual_version" "$actual_build" "$DEVELOPER_DIR"
