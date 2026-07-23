#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-3001}"
HEALTH_URL="http://127.0.0.1:$PORT/health"
BACKEND_PID=""

cleanup() {
  if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

cd "$ROOT_DIR"

backend_is_healthy() {
  curl --silent --fail --max-time 2 "$HEALTH_URL" 2>/dev/null \
    | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'
}

if backend_is_healthy; then
  echo "[macOS-dev] using the backend already running on port $PORT"
else
  [ -x "$ROOT_DIR/.venv/bin/python" ] || {
    echo "[macOS-dev] local backend is not set up; run npm run setup first" >&2
    exit 1
  }

  echo "[macOS-dev] starting the local API, workers, and Gmail sync poller"
  bash "$ROOT_DIR/scripts/dev-backend-runtime.sh" &
  BACKEND_PID="$!"

  backend_ready=0
  for _ in {1..90}; do
    if backend_is_healthy; then
      backend_ready=1
      break
    fi
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
      wait "$BACKEND_PID" || true
      echo "[macOS-dev] local backend stopped before it became ready" >&2
      exit 1
    fi
    sleep 1
  done

  [ "$backend_ready" = "1" ] || {
    echo "[macOS-dev] local backend did not become healthy within 90 seconds" >&2
    exit 1
  }
fi

echo "[macOS-dev] building and opening the native Mac app"
npm run macos:run

if [ -n "$BACKEND_PID" ]; then
  echo "[macOS-dev] app opened; press Control-C here to stop the local backend"
  wait "$BACKEND_PID"
fi
