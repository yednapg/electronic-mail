#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE_DEPS_DIR="$ROOT_DIR/node_modules"
VENV_DIR="$ROOT_DIR/.venv"

echo "==> Decision Pipeline bootstrap started"

if [ ! -d "$NODE_DEPS_DIR" ]; then
  echo "==> Installing Node dependencies (npm install)"
  (cd "$ROOT_DIR" && npm install)
else
  echo "==> Node dependencies already installed"
fi

for name in backend web; do
  source_file="$ROOT_DIR/$name/.env.example"
  target_file="$ROOT_DIR/$name/.env"
  if [ -f "$source_file" ] && [ ! -f "$target_file" ]; then
    cp "$source_file" "$target_file"
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
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$ROOT_DIR/backend/requirements.txt"

echo "==> Creating local SQLite db schema"
"$VENV_DIR/bin/python" "$ROOT_DIR/backend/db_init.py"

echo "==> Bootstrap complete"
echo "Frontend: npm run dev"
echo "Backend: cd backend && ../.venv/bin/python -m uvicorn app.main:app --reload --port 3001"
