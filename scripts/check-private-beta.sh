#!/usr/bin/env bash

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=0

# shellcheck source=private-beta-toolchain.sh
source "$ROOT_DIR/scripts/private-beta-toolchain.sh"

pass() {
  echo "[PASS] $*"
}

warn() {
  echo "[WARN] $*"
}

fail() {
  echo "[FAIL] $*" >&2
  FAILURES=$((FAILURES + 1))
}

cd "$ROOT_DIR"
echo "==> Checking Electronic Mail private beta"

if ! activate_private_beta_toolchain check; then
  fail "The pinned Node/Python toolchain could not be activated"
fi

if [ "$(uname -s)" != "Darwin" ]; then
  fail "macOS is required"
else
  macos_version="$(sw_vers -productVersion 2>/dev/null || true)"
  macos_major="${macos_version%%.*}"
  if [[ "$macos_major" =~ ^[0-9]+$ ]] && [ "$macos_major" -ge 14 ]; then
    pass "macOS $macos_version"
  else
    fail "macOS 14 or newer is required (found ${macos_version:-unknown})"
  fi
fi

xcode_version="$(bash "$ROOT_DIR/scripts/xcode.sh" -version 2>/dev/null | head -n 1)"
if [ -n "$xcode_version" ]; then
  pass "$xcode_version"
else
  fail "Full Xcode is unavailable; install Xcode and open it once"
fi

required_node="$(tr -d '[:space:]' < "$ROOT_DIR/.nvmrc")"
actual_node="$(node -p 'process.versions.node' 2>/dev/null || true)"
if [ "$actual_node" = "$required_node" ]; then
  pass "Node $actual_node"
else
  fail "Node $required_node is required (found ${actual_node:-missing})"
fi

actual_npm="$(npm --version 2>/dev/null || true)"
npm_major="${actual_npm%%.*}"
if [ "$npm_major" = "10" ] || [ "$npm_major" = "11" ]; then
  pass "npm $actual_npm"
else
  fail "npm 10 or 11 is required (found ${actual_npm:-missing})"
fi

required_python="$(tr -d '[:space:]' < "$ROOT_DIR/.python-version")"
actual_python="$(python3 -c 'import platform; print(platform.python_version())' 2>/dev/null || true)"
if [ "$actual_python" = "$required_python" ]; then
  pass "Python $actual_python"
else
  fail "Python $required_python is required (found ${actual_python:-missing})"
fi

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  venv_python="$($ROOT_DIR/.venv/bin/python -c 'import platform; print(platform.python_version())' 2>/dev/null || true)"
  if [ "$venv_python" = "$required_python" ]; then
    pass "Python virtual environment"
  else
    fail "Recreate .venv with Python $required_python"
  fi
else
  fail "Python virtual environment is missing; run npm run beta:setup"
fi

if [ -d "$ROOT_DIR/node_modules" ]; then
  pass "Node dependencies"
else
  fail "Node dependencies are missing; run npm run beta:setup"
fi

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/private_beta_env.py" check || FAILURES=$((FAILURES + 1))
fi

if [ -x "$ROOT_DIR/.venv/bin/python" ] && PYTHONPATH="$ROOT_DIR/backend" "$ROOT_DIR/.venv/bin/python" -m app.schema_check --wait-seconds 0 >/dev/null 2>&1; then
  pass "Local Postgres and schema"
else
  fail "Local Postgres or its schema is unavailable; run npm run beta:setup"
fi

if curl --silent --fail --max-time 2 http://127.0.0.1:3001/health 2>/dev/null | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; then
  pass "Electronic Mail backend is already healthy on port 3001"
elif command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:3001 -sTCP:LISTEN >/dev/null 2>&1; then
  fail "Port 3001 is occupied by another process"
else
  pass "Port 3001 is available"
fi

branch="$(git branch --show-current 2>/dev/null || true)"
commit="$(git rev-parse --short=12 HEAD 2>/dev/null || true)"
if [ -n "$commit" ]; then
  pass "Source ${branch:-detached}@$commit"
fi
if [ -n "$(git status --porcelain --untracked-files=all 2>/dev/null)" ]; then
  warn "The working tree has local changes; include commit $commit in bug reports"
fi

if [ "$FAILURES" -ne 0 ]; then
  echo "Private beta check failed with $FAILURES problem(s)." >&2
  exit 1
fi

echo "==> Private beta check passed"
