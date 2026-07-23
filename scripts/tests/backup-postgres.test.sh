#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT_DIR/scripts/backup-postgres.sh"
STUB="$ROOT_DIR/scripts/tests/fixtures/backup-pg-dump-stub.sh"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/electronic-mail-backup-test.XXXXXX")"
trap 'rm -rf "$TEST_DIR"' EXIT

fail() {
  echo "backup-postgres test failed: $*" >&2
  exit 1
}

assert_file_absent() {
  [ ! -e "$1" ] || fail "expected $1 to be absent"
}

fake_bin="$TEST_DIR/bin"
mkdir -p "$fake_bin"
ln -s "$STUB" "$fake_bin/pg_dump"
test_path="$fake_bin:$PATH"

success_dir="$TEST_DIR/success"
success_log="$TEST_DIR/success-pg-dump.log"
mkdir -p "$success_dir"
printf 'expired backup\n' > "$success_dir/electronic-mail-20200101T000000Z.dump"
printf 'expired checksum\n' > "$success_dir/electronic-mail-20200101T000000Z.dump.sha256"
touch -t 202001010000 "$success_dir/electronic-mail-20200101T000000Z.dump" "$success_dir/electronic-mail-20200101T000000Z.dump.sha256"

PATH="$test_path" \
DATABASE_URL='postgresql://backup_user:s3cr%3At@db.internal:5433/electronic_mail?sslmode=require' \
BACKUP_DIR="$success_dir" \
RETENTION_DAYS=14 \
BACKUP_TEST_LOG="$success_log" \
bash "$SCRIPT" > "$TEST_DIR/success.stdout"

assert_file_absent "$success_dir/electronic-mail-20200101T000000Z.dump"
assert_file_absent "$success_dir/electronic-mail-20200101T000000Z.dump.sha256"
success_dump="$(find "$success_dir" -type f -name 'electronic-mail-*.dump' -print -quit)"
[ -n "$success_dump" ] || fail "successful backup did not create a dump"
[ -s "$success_dump.sha256" ] || fail "successful backup did not create a checksum"
expected_checksum="$(awk 'NR == 1 { print $1 }' "$success_dump.sha256")"
if command -v sha256sum >/dev/null 2>&1; then
  actual_checksum="$(sha256sum "$success_dump" | awk '{ print $1 }')"
else
  actual_checksum="$(shasum -a 256 "$success_dump" | awk '{ print $1 }')"
fi
[ "$expected_checksum" = "$actual_checksum" ] || fail "backup checksum does not match the dump"

grep -Fxq 'DATABASE_URL=' "$success_log" || fail "pg_dump inherited DATABASE_URL"
grep -Fxq 'PGDATABASE=postgresql://backup_user@db.internal:5433/electronic_mail?sslmode=require' "$success_log" \
  || fail "pg_dump did not receive the sanitized connection URL"
grep -Fxq 'PGPASS=*:*:*:*:s3cr\:t' "$success_log" || fail "PGPASSFILE did not contain the decoded escaped password"
if grep -Eq 'ARG=.*(--dbname|s3cr|postgresql://)' "$success_log"; then
  fail "pg_dump arguments exposed connection details"
fi
pgpass_path="$(awk -F= '$1 == "PGPASSFILE" { print substr($0, index($0, "=") + 1) }' "$success_log")"
[ -n "$pgpass_path" ] || fail "pg_dump did not receive PGPASSFILE"
assert_file_absent "$pgpass_path"

failure_dir="$TEST_DIR/failure"
failure_log="$TEST_DIR/failure-pg-dump.log"
mkdir -p "$failure_dir"
printf 'expired backup\n' > "$failure_dir/electronic-mail-20200101T000000Z.dump"
printf 'expired checksum\n' > "$failure_dir/electronic-mail-20200101T000000Z.dump.sha256"
touch -t 202001010000 "$failure_dir/electronic-mail-20200101T000000Z.dump" "$failure_dir/electronic-mail-20200101T000000Z.dump.sha256"

set +e
PATH="$test_path" \
DATABASE_URL='postgresql://backup_user:failure-secret@db.internal/electronic_mail' \
BACKUP_DIR="$failure_dir" \
RETENTION_DAYS=14 \
BACKUP_TEST_LOG="$failure_log" \
BACKUP_TEST_FAIL=1 \
bash "$SCRIPT" > "$TEST_DIR/failure.stdout" 2> "$TEST_DIR/failure.stderr"
failure_status=$?
set -e
[ "$failure_status" -eq 42 ] || fail "pg_dump failure status was not preserved"
[ -f "$failure_dir/electronic-mail-20200101T000000Z.dump" ] \
  || fail "failed backup pruned the last known-good dump"
[ -f "$failure_dir/electronic-mail-20200101T000000Z.dump.sha256" ] \
  || fail "failed backup pruned the last known-good checksum"
if find "$failure_dir" -type f -name '*.partial' -print -quit | grep -q .; then
  fail "partial dump remained after pg_dump failed"
fi
[ ! -d "$failure_dir/.electronic-mail-backup.lock" ] || fail "backup lock remained after failure"

locked_dir="$TEST_DIR/locked"
mkdir -p "$locked_dir/.electronic-mail-backup.lock"
set +e
PATH="$test_path" \
DATABASE_URL='postgresql://backup_user:secret@db.internal/electronic_mail' \
BACKUP_DIR="$locked_dir" \
RETENTION_DAYS=14 \
BACKUP_TEST_LOG="$TEST_DIR/locked.log" \
bash "$SCRIPT" > "$TEST_DIR/locked.stdout" 2> "$TEST_DIR/locked.stderr"
locked_status=$?
set -e
[ "$locked_status" -ne 0 ] || fail "concurrent backup lock was ignored"
grep -Fq 'Another backup is already running' "$TEST_DIR/locked.stderr" \
  || fail "concurrent backup did not report the lock"
[ ! -e "$TEST_DIR/locked.log" ] || fail "pg_dump ran while the backup lock was held"

for invalid_retention in 0 014 366 14days; do
  set +e
  PATH="$test_path" \
  DATABASE_URL='postgresql://backup_user:secret@db.internal/electronic_mail' \
  BACKUP_DIR="$TEST_DIR/invalid-retention" \
  RETENTION_DAYS="$invalid_retention" \
  BACKUP_TEST_LOG="$TEST_DIR/invalid-retention.log" \
  bash "$SCRIPT" > "$TEST_DIR/invalid-retention.stdout" 2> "$TEST_DIR/invalid-retention.stderr"
  invalid_status=$?
  set -e
  [ "$invalid_status" -ne 0 ] || fail "RETENTION_DAYS=$invalid_retention was accepted"
  grep -Fq 'RETENTION_DAYS must be an integer from 1 through 365' "$TEST_DIR/invalid-retention.stderr" \
    || fail "RETENTION_DAYS=$invalid_retention did not produce the validation error"
done

echo "backup-postgres security and retention tests passed"
