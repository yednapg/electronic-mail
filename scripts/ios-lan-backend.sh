#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-start}"
BACKEND_PORT="${ELECTRONIC_MAIL_BACKEND_PORT:-3001}"
LAN_PORT="${ELECTRONIC_MAIL_IOS_LAN_PORT:-3002}"
RUNTIME_DIR="${TMPDIR:-/tmp}"
LOG_FILE="$RUNTIME_DIR/electronic-mail-ios-lan-relay-$LAN_PORT.log"
LAUNCH_LABEL="app.electronicmail.ios-lan-relay.$LAN_PORT"

for port in "$BACKEND_PORT" "$LAN_PORT"; do
  if [[ ! "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
    echo "Backend and LAN ports must be integers between 1 and 65535." >&2
    exit 2
  fi
done

DEFAULT_INTERFACE="$(route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}')"
LAN_ADDRESS="${ELECTRONIC_MAIL_IOS_LAN_ADDRESS:-}"
if [[ -z "$LAN_ADDRESS" && -n "$DEFAULT_INTERFACE" ]]; then
  LAN_ADDRESS="$(ipconfig getifaddr "$DEFAULT_INTERFACE" 2>/dev/null || true)"
fi
if [[ -z "$LAN_ADDRESS" ]]; then
  echo "Could not determine this Mac's LAN address. Set ELECTRONIC_MAIL_IOS_LAN_ADDRESS." >&2
  exit 1
fi

LAN_URL="http://$LAN_ADDRESS:$LAN_PORT"

relay_is_running() {
  launchctl list "$LAUNCH_LABEL" >/dev/null 2>&1
}

case "$ACTION" in
  url)
    printf '%s\n' "$LAN_URL"
    ;;
  status)
    if relay_is_running && curl --noproxy '*' --fail --silent --max-time 2 "$LAN_URL/health" >/dev/null; then
      echo "iPhone LAN backend is available at $LAN_URL"
    else
      echo "iPhone LAN backend is not running." >&2
      exit 1
    fi
    ;;
  stop)
    if relay_is_running; then
      launchctl remove "$LAUNCH_LABEL"
      echo "Stopped the iPhone LAN backend relay."
    else
      echo "The iPhone LAN backend relay was not running."
    fi
    ;;
  start)
    if ! curl --noproxy '*' --fail --silent --max-time 2 "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null; then
      echo "Start the Electronic Mail backend on localhost:$BACKEND_PORT first." >&2
      exit 1
    fi

    if relay_is_running; then
      if curl --noproxy '*' --fail --silent --max-time 2 "$LAN_URL/health" >/dev/null; then
        echo "iPhone LAN backend is already available at $LAN_URL"
        exit 0
      fi
      launchctl remove "$LAUNCH_LABEL" 2>/dev/null || true
    fi

    if lsof -nP -iTCP:"$LAN_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "Port $LAN_PORT is already used by another process." >&2
      exit 1
    fi

    umask 077
    : > "$LOG_FILE"
    launchctl submit -l "$LAUNCH_LABEL" -o "$LOG_FILE" -e "$LOG_FILE" -- /usr/bin/python3 -c '
import asyncio
import sys

listen_port = int(sys.argv[1])
backend_port = int(sys.argv[2])

async def pipe(source, destination):
    try:
        while True:
            data = await source.read(65536)
            if not data:
                break
            destination.write(data)
            await destination.drain()
    finally:
        try:
            destination.write_eof()
        except Exception:
            pass

async def relay(client_reader, client_writer):
    try:
        backend_reader, backend_writer = await asyncio.open_connection("127.0.0.1", backend_port)
        await asyncio.gather(
            pipe(client_reader, backend_writer),
            pipe(backend_reader, client_writer),
        )
        backend_writer.close()
        await backend_writer.wait_closed()
    finally:
        client_writer.close()
        await client_writer.wait_closed()

async def main():
    server = await asyncio.start_server(relay, "0.0.0.0", listen_port)
    async with server:
        await server.serve_forever()

asyncio.run(main())
' "$LAN_PORT" "$BACKEND_PORT"

    for _ in {1..20}; do
      if curl --noproxy '*' --fail --silent --max-time 1 "$LAN_URL/health" >/dev/null; then
        echo "iPhone LAN backend is available at $LAN_URL"
        exit 0
      fi
      sleep 0.1
    done

    launchctl remove "$LAUNCH_LABEL" 2>/dev/null || true
    echo "The iPhone LAN relay did not become healthy. See $LOG_FILE" >&2
    exit 1
    ;;
  *)
    echo "Usage: $0 [start|status|url|stop]" >&2
    exit 2
    ;;
esac
