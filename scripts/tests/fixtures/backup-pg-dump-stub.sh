#!/usr/bin/env bash
set -euo pipefail

: "${BACKUP_TEST_LOG:?BACKUP_TEST_LOG is required}"

{
  printf 'DATABASE_URL=%s\n' "${DATABASE_URL:-}"
  printf 'PGDATABASE=%s\n' "${PGDATABASE:-}"
  printf 'PGPASSFILE=%s\n' "${PGPASSFILE:-}"
  for argument in "$@"; do
    printf 'ARG=%s\n' "$argument"
  done
  if [ -n "${PGPASSFILE:-}" ] && [ -r "$PGPASSFILE" ]; then
    while IFS= read -r line; do
      printf 'PGPASS=%s\n' "$line"
    done < "$PGPASSFILE"
  fi
} > "$BACKUP_TEST_LOG"

if [ "${BACKUP_TEST_FAIL:-0}" = "1" ]; then
  exit 42
fi

output=""
for argument in "$@"; do
  case "$argument" in
    --file=*) output="${argument#--file=}" ;;
  esac
done
[ -n "$output" ] || { echo "stub pg_dump did not receive --file" >&2; exit 2; }
printf 'test backup payload\n' > "$output"
