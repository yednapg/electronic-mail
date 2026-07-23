#!/usr/bin/env bash
set -euo pipefail

command_name="${0##*/}"
case "$command_name" in
  dirname | mkdir | mktemp | python-wrapper)
    mode="$command_name"
    if [ "$command_name" = "python-wrapper" ]; then
      mode=sanitize
      for argument in "$@"; do
        case "$argument" in
          validate-key | encrypt-stdin | decrypt-stdout) mode="$argument" ;;
        esac
      done
    fi
    printf 'CHILD=%s|DATABASE_URL=%s|RESTORE_DATABASE_URL=%s|BACKUP_ENCRYPTION_KEY=%s|PGDATABASE=%s|PGPASSWORD=%s|PGPASSFILE=%s|raw_database_url=%s|raw_restore_database_url=%s|backup_encryption_key=%s\n' \
      "$mode" "${DATABASE_URL:-}" "${RESTORE_DATABASE_URL:-}" "${BACKUP_ENCRYPTION_KEY:-}" \
      "${PGDATABASE:-}" "${PGPASSWORD:-}" "${PGPASSFILE:-}" \
      "${raw_database_url:-}" "${raw_restore_database_url:-}" "${backup_encryption_key:-}" \
      >> "${SECRET_LIFETIME_TEST_LOG:?SECRET_LIFETIME_TEST_LOG is required}"
    case "$command_name" in
      dirname) exec "${SECRET_TEST_REAL_DIRNAME:?}" "$@" ;;
      mkdir) exec "${SECRET_TEST_REAL_MKDIR:?}" "$@" ;;
      mktemp) exec "${SECRET_TEST_REAL_MKTEMP:?}" "$@" ;;
      python-wrapper) exec "${SECRET_TEST_REAL_PYTHON:?}" "$@" ;;
    esac
    ;;
esac

: "${RESTORE_TEST_LOG:?RESTORE_TEST_LOG is required}"

input_content=""
if ! IFS= read -r input_content; then
  # Authentication or checksum failure intentionally closes stdin without
  # invoking restore behavior.
  exit 0
fi
cat >/dev/null

{
  printf 'DATABASE_URL=%s\n' "${DATABASE_URL:-}"
  printf 'RESTORE_DATABASE_URL=%s\n' "${RESTORE_DATABASE_URL:-}"
  printf 'BACKUP_ENCRYPTION_KEY=%s\n' "${BACKUP_ENCRYPTION_KEY:-}"
  printf 'PGDATABASE=%s\n' "${PGDATABASE:-}"
  printf 'PGPASSWORD=%s\n' "${PGPASSWORD:-}"
  printf 'PGPASSFILE=%s\n' "${PGPASSFILE:-}"
  printf 'PGHOST=%s\n' "${PGHOST:-}"
  printf 'PGHOSTADDR=%s\n' "${PGHOSTADDR:-}"
  printf 'PGPORT=%s\n' "${PGPORT:-}"
  printf 'PGUSER=%s\n' "${PGUSER:-}"
  printf 'PGSERVICE=%s\n' "${PGSERVICE:-}"
  printf 'PGSERVICEFILE=%s\n' "${PGSERVICEFILE:-}"
  if [ -n "${PGPASSFILE:-}" ] && [ -f "$PGPASSFILE" ]; then
    printf 'PGPASS=%s\n' "$(tr -d '\n' < "$PGPASSFILE")"
    printf 'PGPASSMODE=%s\n' "$(stat -f '%Lp' "$PGPASSFILE" 2>/dev/null || stat -c '%a' "$PGPASSFILE")"
  fi
  for argument in "$@"; do
    printf 'ARG=%s\n' "$argument"
  done
  printf 'INPUT_SOURCE=stdin\n'
  printf 'INPUT_CONTENT=%s\n' "$input_content"
} > "$RESTORE_TEST_LOG"

if [ "${RESTORE_TEST_FAIL:-0}" = "1" ]; then
  exit 43
fi
