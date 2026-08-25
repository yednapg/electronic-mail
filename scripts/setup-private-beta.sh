#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_EMAIL="${1:-}"

# shellcheck source=private-beta-toolchain.sh
source "$ROOT_DIR/scripts/private-beta-toolchain.sh"

cleanup_toolchain() {
  if [ -n "${PRIVATE_BETA_TOOLCHAIN_TMP:-}" ] && [ -d "$PRIVATE_BETA_TOOLCHAIN_TMP" ]; then
    rm -rf "$PRIVATE_BETA_TOOLCHAIN_TMP"
  fi
}

trap cleanup_toolchain EXIT

if [ "$#" -gt 1 ]; then
  echo "Usage: npm run beta:setup -- [gmail-address]" >&2
  exit 1
fi

if [ "$(uname -s)" != "Darwin" ]; then
  echo "The Electronic Mail private beta requires macOS 14 or newer." >&2
  exit 1
fi

cd "$ROOT_DIR"

echo "==> Selecting the pinned Node and Python versions"
activate_private_beta_toolchain install

echo "==> Installing exact dependencies and preparing local Postgres"
SETUP_DATABASE=1 bash "$ROOT_DIR/scripts/bootstrap.sh"

echo "==> Preparing safe private-beta environment values"
prepare_args=(prepare)
if [ -n "$TEST_EMAIL" ]; then
  prepare_args+=(--email "$TEST_EMAIL")
fi
"$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/private_beta_env.py" "${prepare_args[@]}"

echo "==> Private beta setup is complete"
if [ -z "$TEST_EMAIL" ]; then
  echo "Add ALLOWED_EMAILS=<your Gmail address> to backend/.env.local."
fi
echo "Create the Google OAuth client described in docs/PRIVATE_BETA.md, add its credentials to backend/.env.local, then run:"
echo "  npm run beta:check"
echo "  npm run beta:start"
