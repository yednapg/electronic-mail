#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-run}"
DEVICE_ID="${IOS_DEVICE_ID:-}"
WORKSPACE="macos/ElectronicMail/ElectronicMail.xcworkspace"
SCHEME="ElectronicMailiOS"
DERIVED_DATA="/tmp/ElectronicMailIOSDerivedData"
APP_PATH="$DERIVED_DATA/Build/Products/Debug-iphoneos/ElectronicMailiOS.app"
BUNDLE_ID="app.electronicmail.ios"

if [[ -z "$DEVICE_ID" ]]; then
  echo "Set IOS_DEVICE_ID to the iPhone UDID from: npm run ios:devices"
  exit 1
fi

build_settings=(
  "CODE_SIGN_STYLE=Automatic"
)

if [[ -n "${IOS_DEVELOPMENT_TEAM:-}" ]]; then
  build_settings+=("DEVELOPMENT_TEAM=$IOS_DEVELOPMENT_TEAM")
fi

if [[ -n "${ELECTRONIC_MAIL_IOS_BACKEND_URL:-}" ]]; then
  build_settings+=("ELECTRONIC_MAIL_IOS_BACKEND_URL=$ELECTRONIC_MAIL_IOS_BACKEND_URL")
fi

xcodebuild_common=(
  -workspace "$WORKSPACE"
  -scheme "$SCHEME"
  -destination "id=$DEVICE_ID"
  -destination-timeout 60
  COMPILER_INDEX_STORE_ENABLE=NO
  -jobs 2
  -derivedDataPath "$DERIVED_DATA"
  -allowProvisioningUpdates
  -allowProvisioningDeviceRegistration
  "${build_settings[@]}"
)

case "$ACTION" in
  build)
    xcodebuild build "${xcodebuild_common[@]}"
    xcrun devicectl device install app --device "$DEVICE_ID" "$APP_PATH"
    ;;
  run)
    "$0" build
    xcrun devicectl device process launch --device "$DEVICE_ID" --terminate-existing "$BUNDLE_ID"
    ;;
  test)
    xcodebuild test "${xcodebuild_common[@]}"
    ;;
  *)
    echo "Usage: IOS_DEVICE_ID=<iphone-udid> $0 [build|run|test]"
    exit 2
    ;;
esac
