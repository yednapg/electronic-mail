#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDS=()

cleanup() {
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}

trap cleanup EXIT INT TERM

cd "$ROOT_DIR"

if [[ "${SKIP_MIGRATIONS:-0}" != "1" ]]; then
  echo "[backend-runtime] applying database migrations"
  npm run db:migrate
fi

echo "[backend-runtime] starting API on port ${PORT:-3001}"
(cd backend && ../.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-3001}" --no-access-log) &
PIDS+=("$!")

echo "[backend-runtime] starting fast worker for critical,default queues"
(cd backend && ../.venv/bin/python -m app.workers.main --queues critical,default) &
PIDS+=("$!")

echo "[backend-runtime] starting reader worker for reader queue"
(cd backend && ../.venv/bin/python -m app.workers.main --queues reader --sleep 1) &
PIDS+=("$!")

echo "[backend-runtime] starting AI worker for ai queue"
(cd backend && ../.venv/bin/python -m app.workers.main --queues ai --sleep 1) &
PIDS+=("$!")

echo "[backend-runtime] starting slow worker for slow queue"
(cd backend && ../.venv/bin/python -m app.workers.main --queues slow --sleep 5) &
PIDS+=("$!")

echo "[backend-runtime] starting Gmail sync poller"
(cd backend && ../.venv/bin/python -m app.workers.gmail_poller --interval "${GMAIL_POLL_INTERVAL_SECONDS:-30}") &
PIDS+=("$!")

echo "[backend-runtime] started pids: ${PIDS[*]}"
echo "[backend-runtime] /ready should be ready and /v1/ops/health should show fresh workers plus gmail_poll after login"

while true; do
  for pid in "${PIDS[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid" 2>/dev/null
      exit_code=$?
      echo "[backend-runtime] process $pid exited with status $exit_code; stopping runtime"
      exit "$exit_code"
    fi
  done
  sleep 1
done
