#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-run}"
WORKSPACE="macos/ElectronicMail/ElectronicMail.xcworkspace"
SCHEME="ElectronicMailiOS"
DERIVED_DATA="/tmp/ElectronicMailIOSSimulatorDerivedData"
APP_PATH="$DERIVED_DATA/Build/Products/Debug-iphonesimulator/ElectronicMailiOS.app"
BUNDLE_ID="app.electronicmail.ios"

DEVICE_ID="${IOS_SIMULATOR_ID:-}"
if [[ -z "$DEVICE_ID" ]]; then
  DEVICE_ID="$(bash scripts/xcrun.sh simctl list devices booted | sed -nE 's/.*\(([0-9A-F-]{36})\) \(Booted\).*/\1/p' | head -1)"
fi

if [[ -z "$DEVICE_ID" ]]; then
  echo "Boot an iPhone Simulator first, or set IOS_SIMULATOR_ID to a booted simulator UDID." >&2
  exit 1
fi

xcodebuild_common=(
  -workspace "$WORKSPACE"
  -scheme "$SCHEME"
  -destination "id=$DEVICE_ID"
  -destination-timeout 60
  COMPILER_INDEX_STORE_ENABLE=NO
  -jobs 2
  -derivedDataPath "$DERIVED_DATA"
  CODE_SIGNING_ALLOWED=NO
)

case "$ACTION" in
  build)
    bash scripts/xcode.sh build "${xcodebuild_common[@]}"
    ;;
  run)
    "$0" build
    bash scripts/xcrun.sh simctl install "$DEVICE_ID" "$APP_PATH"
    bash scripts/xcrun.sh simctl launch --terminate-running-process "$DEVICE_ID" "$BUNDLE_ID" -ElectronicMailDemo
    ;;
  test)
    bash scripts/xcode.sh test "${xcodebuild_common[@]}" -only-testing:ElectronicMailiOSTests
    ;;
  ui-test)
    bash scripts/xcode.sh test "${xcodebuild_common[@]}" -only-testing:ElectronicMailiOSUITests
    ;;
  *)
    echo "Usage: $0 [build|run|test|ui-test]" >&2
    exit 2
    ;;
esac
