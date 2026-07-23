#!/usr/bin/env bash
set -euo pipefail

: "${RESTORE_TEST_LOG:?RESTORE_TEST_LOG is required}"

{
  printf 'PGDATABASE=%s\n' "${PGDATABASE:-}"
  printf 'PGPASSFILE=%s\n' "${PGPASSFILE:-}"
  if [ -n "${PGPASSFILE:-}" ] && [ -f "$PGPASSFILE" ]; then
    printf 'PGPASS=%s\n' "$(tr -d '\n' < "$PGPASSFILE")"
    printf 'PGPASSMODE=%s\n' "$(stat -f '%Lp' "$PGPASSFILE" 2>/dev/null || stat -c '%a' "$PGPASSFILE")"
  fi
  for argument in "$@"; do
    printf 'ARG=%s\n' "$argument"
  done
} > "$RESTORE_TEST_LOG"

if [ "${RESTORE_TEST_FAIL:-0}" = "1" ]; then
  exit 43
fi
