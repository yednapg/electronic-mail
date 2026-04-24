#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${ROOT_DIR}/ios/ElectronicMail/ElectronicMail.xcworkspace"
PROJECT_FILE="${ROOT_DIR}/ios/ElectronicMail/Project.swift"
SCHEME="${IOS_SCHEME:-ElectronicMail}"
BUNDLE_ID="${IOS_BUNDLE_ID:-app.electronicmail.ios}"
DERIVED_DATA="${IOS_DERIVED_DATA:-${ROOT_DIR}/.build/ios/DerivedData}"
TUIST_STAMP="${ROOT_DIR}/.build/ios/tuist-generated.stamp"

if [[ ! -d "${WORKSPACE}" ]]; then
  echo "Xcode workspace missing. Running Tuist once..."
  (cd "${ROOT_DIR}/ios/ElectronicMail" && tuist generate)
  mkdir -p "$(dirname "${TUIST_STAMP}")"
  touch "${TUIST_STAMP}"
elif [[ ! -f "${TUIST_STAMP}" || "${PROJECT_FILE}" -nt "${TUIST_STAMP}" ]]; then
  echo "Project.swift changed. Regenerating Xcode workspace..."
  (cd "${ROOT_DIR}/ios/ElectronicMail" && tuist generate)
  mkdir -p "$(dirname "${TUIST_STAMP}")"
  touch "${TUIST_STAMP}"
fi

SIMULATOR_ID="${IOS_SIMULATOR_ID:-$(xcrun simctl list devices booted | sed -n 's/.*(\([A-F0-9-]\{36\}\)) (Booted).*/\1/p' | head -n 1)}"

if [[ -z "${SIMULATOR_ID}" ]]; then
  echo "No booted simulator found. Boot a simulator first, or set IOS_SIMULATOR_ID."
  exit 1
fi

mkdir -p "${DERIVED_DATA}"

echo "Building ${SCHEME} for simulator ${SIMULATOR_ID}..."
xcodebuild \
  -quiet \
  -workspace "${WORKSPACE}" \
  -scheme "${SCHEME}" \
  -destination "id=${SIMULATOR_ID}" \
  -derivedDataPath "${DERIVED_DATA}" \
  build

APP_PATH="${DERIVED_DATA}/Build/Products/Debug-iphonesimulator/${SCHEME}.app"

if [[ ! -d "${APP_PATH}" ]]; then
  echo "Built app not found at ${APP_PATH}"
  exit 1
fi

echo "Installing and launching ${BUNDLE_ID}..."
xcrun simctl install "${SIMULATOR_ID}" "${APP_PATH}"
xcrun simctl launch "${SIMULATOR_ID}" "${BUNDLE_ID}" >/dev/null
echo "Launched ${BUNDLE_ID}."
