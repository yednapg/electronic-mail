#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE_DEPS_DIR="$ROOT_DIR/node_modules"
VENV_DIR="$ROOT_DIR/.venv"

echo "==> Electronic Mail bootstrap started"

required_node="$(tr -d '[:space:]' < "$ROOT_DIR/.nvmrc")"
actual_node="$(node -p 'process.versions.node')"
[ "$actual_node" = "$required_node" ] || {
  echo "Node $required_node is required (found $actual_node); activate .nvmrc before setup" >&2
  exit 1
}
required_python="$(tr -d '[:space:]' < "$ROOT_DIR/.python-version")"
actual_python="$(python3 -c 'import platform; print(platform.python_version())')"
[ "$actual_python" = "$required_python" ] || {
  echo "Python $required_python is required (found $actual_python); activate .python-version before setup" >&2
  exit 1
}

if [ "${SKIP_NODE_INSTALL:-0}" = "1" ]; then
  echo "==> Skipping Node dependency install"
else
  echo "==> Installing exact Node dependencies (npm ci)"
  (cd "$ROOT_DIR" && npm ci)
fi

for name in backend web; do
  source_file="$ROOT_DIR/$name/.env.example"
  target_file="$ROOT_DIR/$name/.env"
  if [ -f "$source_file" ] && [ ! -f "$target_file" ]; then
    (umask 077 && cp "$source_file" "$target_file")
    chmod 600 "$target_file"
    echo "==> Created $target_file from example"
  else
    echo "==> Env file ready: $target_file"
  fi
done

if [ ! -d "$VENV_DIR" ]; then
  echo "==> Creating root python virtualenv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
else
  echo "==> Root python virtualenv already exists"
fi

echo "==> Installing Python dependencies for the backend"
"$VENV_DIR/bin/pip" install --upgrade "pip==26.1.2"
"$VENV_DIR/bin/pip" install --no-deps -r "$ROOT_DIR/backend/requirements.lock"
"$VENV_DIR/bin/pip" check

if [ "${SETUP_DATABASE:-0}" = "1" ]; then
  echo "==> Setting up and migrating the local Postgres database"
  (cd "$ROOT_DIR" && npm run db:local:setup)
else
  echo "==> Dependency setup complete; run npm run db:local:setup when a local database is needed"
fi

echo "==> Bootstrap complete"
echo "Frontend: npm run dev"
echo "Backend: npm run backend:dev"
