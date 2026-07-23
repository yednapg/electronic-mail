#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT_DIR/scripts/restore-postgres.sh"
STUB="$ROOT_DIR/scripts/tests/fixtures/restore-pg-restore-stub.sh"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/electronic-mail-restore-test.XXXXXX")"
trap 'rm -rf "$TEST_DIR"' EXIT

fail() {
  echo "restore-postgres test failed: $*" >&2
  exit 1
}

checksum_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1"
  else
    shasum -a 256 "$1"
  fi
}

fake_bin="$TEST_DIR/bin"
mkdir -p "$fake_bin"
ln -s "$STUB" "$fake_bin/pg_restore"
test_path="$fake_bin:$PATH"

backup_file="$TEST_DIR/electronic-mail-test.dump"
printf 'verified backup data\n' > "$backup_file"
checksum_file "$backup_file" > "$backup_file.sha256"

success_log="$TEST_DIR/success-pg-restore.log"
PATH="$test_path" \
CONFIRM_RESTORE=RESTORE \
RESTORE_DATABASE_URL='postgresql://restore_user:s3cr%3At@db.internal:5433/electronic_mail_restore?sslmode=require' \
EXPECTED_RESTORE_DATABASE=electronic_mail_restore \
BACKUP_FILE="$backup_file" \
RESTORE_TEST_LOG="$success_log" \
bash "$SCRIPT" > "$TEST_DIR/success.stdout"

grep -Fxq 'PGDATABASE=postgresql://restore_user@db.internal:5433/electronic_mail_restore?sslmode=require' "$success_log" \
  || fail "pg_restore did not receive the sanitized connection URL"
grep -Fxq 'PGPASS=*:*:*:*:s3cr\:t' "$success_log" \
  || fail "PGPASSFILE did not contain the decoded escaped password"
grep -Fxq 'PGPASSMODE=600' "$success_log" || fail "PGPASSFILE permissions were not 0600"
for required_argument in --clean --if-exists --no-owner --no-acl --exit-on-error "$backup_file"; do
  grep -Fxq "ARG=$required_argument" "$success_log" || fail "missing pg_restore argument $required_argument"
done
if grep -Eq 'ARG=.*(--dbname|s3cr|postgresql://)' "$success_log"; then
  fail "pg_restore arguments exposed connection details"
fi
pgpass_path="$(awk -F= '$1 == "PGPASSFILE" { print substr($0, index($0, "=") + 1) }' "$success_log")"
[ -n "$pgpass_path" ] || fail "pg_restore did not receive PGPASSFILE"
[ ! -e "$pgpass_path" ] || fail "temporary PGPASSFILE was not removed"

failure_log="$TEST_DIR/failure-pg-restore.log"
set +e
PATH="$test_path" \
CONFIRM_RESTORE=RESTORE \
RESTORE_DATABASE_URL='postgresql://restore_user:failure-secret@db.internal/electronic_mail_restore' \
EXPECTED_RESTORE_DATABASE=electronic_mail_restore \
BACKUP_FILE="$backup_file" \
RESTORE_TEST_LOG="$failure_log" \
RESTORE_TEST_FAIL=1 \
bash "$SCRIPT" > "$TEST_DIR/failure.stdout" 2> "$TEST_DIR/failure.stderr"
failure_status=$?
set -e
[ "$failure_status" -eq 43 ] || fail "pg_restore failure status was not preserved"
failure_pgpass_path="$(awk -F= '$1 == "PGPASSFILE" { print substr($0, index($0, "=") + 1) }' "$failure_log")"
[ -n "$failure_pgpass_path" ] && [ ! -e "$failure_pgpass_path" ] \
  || fail "temporary credentials remained after pg_restore failed"

printf 'tampered backup data\n' > "$backup_file"
set +e
PATH="$test_path" \
CONFIRM_RESTORE=RESTORE \
RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal/electronic_mail_restore' \
EXPECTED_RESTORE_DATABASE=electronic_mail_restore \
BACKUP_FILE="$backup_file" \
RESTORE_TEST_LOG="$TEST_DIR/tampered-pg-restore.log" \
bash "$SCRIPT" > "$TEST_DIR/tampered.stdout" 2> "$TEST_DIR/tampered.stderr"
tampered_status=$?
set -e
[ "$tampered_status" -ne 0 ] || fail "tampered backup was accepted"
grep -Fq 'Backup checksum verification failed' "$TEST_DIR/tampered.stderr" \
  || fail "tampered backup did not report checksum failure"
[ ! -e "$TEST_DIR/tampered-pg-restore.log" ] || fail "pg_restore ran for a tampered backup"

checksum_file "$backup_file" > "$backup_file.sha256"
set +e
PATH="$test_path" \
CONFIRM_RESTORE=RESTORE \
RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal/electronic_mail_restore' \
EXPECTED_RESTORE_DATABASE=wrong_database \
BACKUP_FILE="$backup_file" \
RESTORE_TEST_LOG="$TEST_DIR/wrong-target-pg-restore.log" \
bash "$SCRIPT" > "$TEST_DIR/wrong-target.stdout" 2> "$TEST_DIR/wrong-target.stderr"
wrong_target_status=$?
set -e
[ "$wrong_target_status" -ne 0 ] || fail "restore target mismatch was accepted"
grep -Fq 'Restore database mismatch' "$TEST_DIR/wrong-target.stderr" \
  || fail "restore target mismatch did not report the database names"
[ ! -e "$TEST_DIR/wrong-target-pg-restore.log" ] || fail "pg_restore ran for a mismatched target"

set +e
PATH="$test_path" \
CONFIRM_RESTORE=RESTORE \
RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal/electronic_mail' \
EXPECTED_RESTORE_DATABASE=electronic_mail \
BACKUP_FILE="$backup_file" \
RESTORE_TEST_LOG="$TEST_DIR/protected-pg-restore.log" \
bash "$SCRIPT" > "$TEST_DIR/protected.stdout" 2> "$TEST_DIR/protected.stderr"
protected_status=$?
set -e
[ "$protected_status" -ne 0 ] || fail "primary database was accepted as an isolated restore target"
grep -Fq 'Refusing to treat protected database electronic_mail as an isolated restore target' "$TEST_DIR/protected.stderr" \
  || fail "protected target did not report the isolation failure"
[ ! -e "$TEST_DIR/protected-pg-restore.log" ] || fail "pg_restore ran against a protected isolated target"

echo "restore-postgres credential and checksum tests passed"
