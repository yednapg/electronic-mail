#!/usr/bin/env bash
set -euo pipefail

if [ -n "${DEVELOPER_DIR:-}" ] && [ -x "$DEVELOPER_DIR/usr/bin/xcodebuild" ]; then
  :
elif [ -x /Applications/Xcode.app/Contents/Developer/usr/bin/xcodebuild ]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
elif [ -x /Applications/Xcode-beta.app/Contents/Developer/usr/bin/xcodebuild ]; then
  export DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer
else
  echo "Full Xcode is required. Install Xcode or set DEVELOPER_DIR to its Contents/Developer directory." >&2
  exit 1
fi

exec /usr/bin/xcrun "$@"
