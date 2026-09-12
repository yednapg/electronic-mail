#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-run}"
DEVICE_ID="${IOS_DEVICE_ID:-}"
WORKSPACE="macos/ElectronicMail/ElectronicMail.xcworkspace"
SCHEME="ElectronicMailiOS"
DERIVED_DATA="/tmp/ElectronicMailIOSDerivedData"
APP_PATH="$DERIVED_DATA/Build/Products/Debug-iphoneos/ElectronicMailiOS.app"
BUNDLE_ID="app.electronicmail.ios"

if [[ -z "${ELECTRONIC_MAIL_IOS_BACKEND_URL:-}" ]]; then
  bash scripts/ios-lan-backend.sh start
  ELECTRONIC_MAIL_IOS_BACKEND_URL="$(bash scripts/ios-lan-backend.sh url)"
fi

if [[ ! "$ELECTRONIC_MAIL_IOS_BACKEND_URL" =~ ^https?://[^/]+ ]]; then
  echo "ELECTRONIC_MAIL_IOS_BACKEND_URL must be an absolute http(s) URL." >&2
  exit 1
fi

if ! curl --noproxy '*' --fail --silent --max-time 5 "$ELECTRONIC_MAIL_IOS_BACKEND_URL/health" >/dev/null; then
  echo "The iPhone cannot use an unhealthy backend: $ELECTRONIC_MAIL_IOS_BACKEND_URL" >&2
  exit 1
fi

if [[ -z "$DEVICE_ID" ]]; then
  DEVICE_IDS="$(
    bash scripts/xcode.sh -showdestinations -workspace "$WORKSPACE" -scheme "$SCHEME" 2>&1 \
      | sed -nE 's/.*platform:iOS, arch:[^,]+, id:([^,]+), name:.*/\1/p'
  )"
  DEVICE_COUNT="$(printf '%s\n' "$DEVICE_IDS" | sed '/^$/d' | wc -l | tr -d ' ')"
  if [[ "$DEVICE_COUNT" != "1" ]]; then
    echo "Set IOS_DEVICE_ID because $DEVICE_COUNT physical iOS devices were discovered." >&2
    printf '%s\n' "$DEVICE_IDS" >&2
    exit 1
  fi
  DEVICE_ID="$DEVICE_IDS"
fi

build_settings=(
  "CODE_SIGN_STYLE=Automatic"
)

if [[ -n "${IOS_DEVELOPMENT_TEAM:-}" ]]; then
  build_settings+=("DEVELOPMENT_TEAM=$IOS_DEVELOPMENT_TEAM")
fi

build_settings+=("ELECTRONIC_MAIL_IOS_BACKEND_URL=$ELECTRONIC_MAIL_IOS_BACKEND_URL")

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
    bash scripts/xcode.sh build "${xcodebuild_common[@]}"
    bash scripts/xcrun.sh devicectl device install app --device "$DEVICE_ID" "$APP_PATH"
    ;;
  run)
    "$0" build
    bash scripts/xcrun.sh devicectl device process launch --device "$DEVICE_ID" --terminate-existing "$BUNDLE_ID"
    ;;
  test)
    bash scripts/xcode.sh test "${xcodebuild_common[@]}"
    ;;
  *)
    echo "Usage: IOS_DEVICE_ID=<iphone-udid> $0 [build|run|test]"
    exit 2
    ;;
esac
