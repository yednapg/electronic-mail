#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE_DEPS_DIR="$ROOT_DIR/node_modules"
VENV_DIR="$ROOT_DIR/.venv"

echo "==> ElectronicMail bootstrap started"

if [ ! -d "$NODE_DEPS_DIR" ]; then
  echo "==> Installing Node dependencies (npm install)"
  (cd "$ROOT_DIR" && npm install)
else
  echo "==> Node dependencies already installed"
fi

for name in api web ai; do
  source_file="$ROOT_DIR/apps/$name/.env.example"
  target_file="$ROOT_DIR/apps/$name/.env"
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

echo "==> Installing ai service python dependencies"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$ROOT_DIR/apps/ai/requirements.txt"

cd "$ROOT_DIR"
echo "==> Generating Prisma client"
npm run db:generate

echo "==> Creating local SQLite db schema"
npm run db:push

echo "==> Bootstrap complete"
echo "Next: run npm run dev to start web, API, and AI service."
