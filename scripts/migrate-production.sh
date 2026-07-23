#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ "${APP_ENV:-}" = "staging" ] || [ "${APP_ENV:-}" = "production" ] || { echo "APP_ENV must be staging or production" >&2; exit 1; }
[ -n "${DATABASE_URL:-}" ] || { echo "DATABASE_URL is required" >&2; exit 1; }

cd "$ROOT_DIR/backend"
PYTHONPATH=. ../.venv/bin/python -m app.deploy_check
../.venv/bin/alembic heads
../.venv/bin/alembic upgrade head
../.venv/bin/alembic current --check-heads
