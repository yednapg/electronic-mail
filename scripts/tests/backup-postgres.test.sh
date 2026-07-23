#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT_DIR/scripts/backup-postgres.sh"
CRYPTO_SCRIPT="$ROOT_DIR/scripts/postgres-backup-crypto.py"
STUB="$ROOT_DIR/scripts/tests/fixtures/backup-pg-dump-stub.sh"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/electronic-mail-backup-test.XXXXXX")"
trap 'rm -rf "$TEST_DIR"' EXIT
BACKUP_KEY=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

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
ln -s "$STUB" "$fake_bin/dirname"
ln -s "$STUB" "$fake_bin/mkdir"
ln -s "$STUB" "$fake_bin/mktemp"
ln -s "$STUB" "$fake_bin/python-wrapper"
test_path="$fake_bin:$PATH"
secret_lifetime_log="$TEST_DIR/secret-lifetime.log"
export SECRET_LIFETIME_TEST_LOG="$secret_lifetime_log"
export SECRET_TEST_REAL_DIRNAME="$(type -P dirname)"
export SECRET_TEST_REAL_MKDIR="$(type -P mkdir)"
export SECRET_TEST_REAL_MKTEMP="$(type -P mktemp)"
export SECRET_TEST_REAL_PYTHON="$ROOT_DIR/.venv/bin/python"
export BACKUP_CRYPTO_PYTHON="$fake_bin/python-wrapper"

success_dir="$TEST_DIR/success"
success_log="$TEST_DIR/success-pg-dump.log"
mkdir -p "$success_dir"
printf 'expired backup\n' > "$success_dir/electronic-mail-20200101T000000Z.dump.enc"
printf 'expired checksum\n' > "$success_dir/electronic-mail-20200101T000000Z.dump.enc.sha256"
touch -t 202001010000 "$success_dir/electronic-mail-20200101T000000Z.dump.enc" "$success_dir/electronic-mail-20200101T000000Z.dump.enc.sha256"
boundary_old="$success_dir/electronic-mail-boundary-old.dump.enc"
boundary_young="$success_dir/electronic-mail-boundary-young.dump.enc"
boundary_old_checksum="$boundary_old.sha256"
boundary_young_checksum="$boundary_young.sha256"
printf 'older than the exact cutoff\n' > "$boundary_old"
printf 'newer than the exact cutoff\n' > "$boundary_young"
printf 'old checksum\n' > "$boundary_old_checksum"
printf 'young checksum\n' > "$boundary_young_checksum"
python3 - "$boundary_old" "$boundary_young" "$boundary_old_checksum" "$boundary_young_checksum" <<'PY'
import os
import sys
import time

now = time.time()
retention_seconds = 14 * 86_400
for path in (sys.argv[1], sys.argv[3]):
    os.utime(path, (now - retention_seconds - 5, now - retention_seconds - 5))
for path in (sys.argv[2], sys.argv[4]):
    os.utime(path, (now - retention_seconds + 5, now - retention_seconds + 5))
PY

success_database_url='postgresql://backup_user:s3cr%3At@db.internal:5433/electronic_mail?sslmode=require'
PATH="$test_path" \
DATABASE_URL="$success_database_url" \
BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
raw_database_url=preexported-raw-url backup_encryption_key=preexported-key \
BACKUP_DIR="$success_dir" \
RETENTION_DAYS=14 \
BACKUP_TEST_LOG="$success_log" \
PGHOST=poison-host PGHOSTADDR=203.0.113.10 PGPORT=9999 PGUSER=poison-user \
PGDATABASE=poison-database PGPASSWORD=poison-password PGPASSFILE="$TEST_DIR/poison-pgpass" \
PGSERVICE=poison-service PGSERVICEFILE="$TEST_DIR/poison-service.conf" \
bash "$SCRIPT" > "$TEST_DIR/success.stdout"

assert_file_absent "$success_dir/electronic-mail-20200101T000000Z.dump.enc"
assert_file_absent "$success_dir/electronic-mail-20200101T000000Z.dump.enc.sha256"
assert_file_absent "$boundary_old"
assert_file_absent "$boundary_old_checksum"
[ -f "$boundary_young" ] || fail "backup newer than the exact retention cutoff was removed"
[ -f "$boundary_young_checksum" ] || fail "checksum newer than the exact retention cutoff was removed"
success_dump="$(find "$success_dir" -type f -name 'electronic-mail-*.dump.enc' ! -name 'electronic-mail-boundary-*' -print -quit)"
[ -n "$success_dump" ] || fail "successful backup did not create an encrypted dump"
[ -s "$success_dump.sha256" ] || fail "successful backup did not create a checksum"
expected_checksum="$(awk 'NR == 1 { print $1 }' "$success_dump.sha256")"
if command -v sha256sum >/dev/null 2>&1; then
  actual_checksum="$(sha256sum "$success_dump" | awk '{ print $1 }')"
else
  actual_checksum="$(shasum -a 256 "$success_dump" | awk '{ print $1 }')"
fi
[ "$expected_checksum" = "$actual_checksum" ] || fail "backup checksum does not match the dump"
if grep -Fq 'test backup payload' "$success_dump"; then
  fail "plaintext pg_dump content was published in the final backup"
fi
if find "$success_dir" -type f -name 'electronic-mail-*.dump' -print -quit | grep -q .; then
  fail "a plaintext final dump was published"
fi
success_mode="$(stat -f '%Lp' "$success_dump" 2>/dev/null || stat -c '%a' "$success_dump")"
[ "$success_mode" = "600" ] || fail "encrypted backup permissions were not 0600"

test_key_file="$TEST_DIR/test-key"
decrypted_dump="$TEST_DIR/decrypted.dump"
printf '%s\n' "$BACKUP_KEY" > "$test_key_file"
chmod 600 "$test_key_file"
"$ROOT_DIR/.venv/bin/python" "$CRYPTO_SCRIPT" decrypt \
  --key-file "$test_key_file" --input "$success_dump" --output "$decrypted_dump"
grep -Fxq 'test backup payload' "$decrypted_dump" || fail "encrypted backup did not decrypt to the pg_dump payload"

grep -Fxq 'DATABASE_URL=' "$success_log" || fail "pg_dump inherited DATABASE_URL"
grep -Fxq 'RESTORE_DATABASE_URL=' "$success_log" || fail "pg_dump inherited RESTORE_DATABASE_URL"
grep -Fxq 'BACKUP_ENCRYPTION_KEY=' "$success_log" || fail "pg_dump inherited BACKUP_ENCRYPTION_KEY"
grep -Fxq 'PGPASSWORD=' "$success_log" || fail "pg_dump inherited PGPASSWORD"
grep -Fxq 'PGDATABASE=postgresql://backup_user@db.internal:5433/electronic_mail?sslmode=require' "$success_log" \
  || fail "pg_dump did not receive the sanitized connection URL"
grep -Fxq 'PGPASS=*:*:*:*:s3cr\:t' "$success_log" || fail "PGPASSFILE did not contain the decoded escaped password"
for cleared_routing_variable in PGHOST PGHOSTADDR PGPORT PGUSER PGSERVICE PGSERVICEFILE; do
  grep -Fxq "$cleared_routing_variable=" "$success_log" \
    || fail "pg_dump inherited $cleared_routing_variable"
done
if grep -Eq 'ARG=.*(--dbname|s3cr|postgresql://)' "$success_log"; then
  fail "pg_dump arguments exposed connection details"
fi
if grep -Eq '^ARG=--file($|=)' "$success_log"; then
  fail "pg_dump wrote plaintext to a file instead of streaming to encryption"
fi
pgpass_path="$(awk -F= '$1 == "PGPASSFILE" { print substr($0, index($0, "=") + 1) }' "$success_log")"
[ -n "$pgpass_path" ] || fail "pg_dump did not receive PGPASSFILE"
assert_file_absent "$pgpass_path"

empty_child_secrets='DATABASE_URL=|RESTORE_DATABASE_URL=|BACKUP_ENCRYPTION_KEY=|PGDATABASE=|PGPASSWORD=|PGPASSFILE=|raw_database_url=|raw_restore_database_url=|backup_encryption_key='
for early_child in dirname mkdir mktemp; do
  grep -Fxq "CHILD=$early_child|$empty_child_secrets" "$secret_lifetime_log" \
    || fail "$early_child inherited a captured backup secret"
done
grep -Fxq "CHILD=validate-key|DATABASE_URL=|RESTORE_DATABASE_URL=|BACKUP_ENCRYPTION_KEY=$BACKUP_KEY|PGDATABASE=|PGPASSWORD=|PGPASSFILE=|raw_database_url=|raw_restore_database_url=|backup_encryption_key=" \
  "$secret_lifetime_log" || fail "key validation inherited the database URL or missed its scoped key"
grep -Fxq "CHILD=sanitize|DATABASE_URL=$success_database_url|RESTORE_DATABASE_URL=|BACKUP_ENCRYPTION_KEY=|PGDATABASE=|PGPASSWORD=|PGPASSFILE=|raw_database_url=|raw_restore_database_url=|backup_encryption_key=" \
  "$secret_lifetime_log" || fail "URL sanitizer inherited the encryption key or missed its scoped URL"
grep -Fxq "CHILD=encrypt-stdin|DATABASE_URL=|RESTORE_DATABASE_URL=|BACKUP_ENCRYPTION_KEY=$BACKUP_KEY|PGDATABASE=|PGPASSWORD=|PGPASSFILE=|raw_database_url=|raw_restore_database_url=|backup_encryption_key=" \
  "$secret_lifetime_log" || fail "encryption inherited the database URL or missed its scoped key"
if grep -Eq '\|(raw_database_url|raw_restore_database_url|backup_encryption_key)=[^|]' "$secret_lifetime_log"; then
  fail "captured backup secrets remained exported to child processes"
fi

xtrace_key=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
xtrace_password=xtrace-backup-password-sentinel
xtrace_dir="$TEST_DIR/xtrace-success"
xtrace_log="$TEST_DIR/xtrace-pg-dump.log"
mkdir -p "$xtrace_dir"
PATH="$test_path" \
DATABASE_URL="postgresql://backup_user:$xtrace_password@db.internal/electronic_mail" \
BACKUP_ENCRYPTION_KEY="$xtrace_key" \
BACKUP_DIR="$xtrace_dir" \
RETENTION_DAYS=14 \
BACKUP_TEST_LOG="$xtrace_log" \
bash -x "$SCRIPT" > "$TEST_DIR/xtrace.stdout" 2> "$TEST_DIR/xtrace.stderr"
grep -Fq 'set +x' "$TEST_DIR/xtrace.stderr" || fail "xtrace regression did not start with tracing enabled"
if grep -Fq "$xtrace_password" "$TEST_DIR/xtrace.stderr"; then
  fail "bash -x exposed the backup URL password"
fi
if grep -Fq "$xtrace_key" "$TEST_DIR/xtrace.stderr"; then
  fail "bash -x exposed the backup encryption key"
fi
xtrace_dump="$(find "$xtrace_dir" -type f -name 'electronic-mail-*.dump.enc' -print -quit)"
[ -n "$xtrace_dump" ] && [ -s "$xtrace_dump.sha256" ] \
  || fail "xtrace-protected backup did not publish a complete encrypted pair"
grep -Fxq 'BACKUP_ENCRYPTION_KEY=' "$xtrace_log" \
  || fail "xtrace-protected backup leaked the key to pg_dump"

failure_dir="$TEST_DIR/failure"
failure_log="$TEST_DIR/failure-pg-dump.log"
mkdir -p "$failure_dir"
printf 'expired backup\n' > "$failure_dir/electronic-mail-20200101T000000Z.dump.enc"
printf 'expired checksum\n' > "$failure_dir/electronic-mail-20200101T000000Z.dump.enc.sha256"
touch -t 202001010000 "$failure_dir/electronic-mail-20200101T000000Z.dump.enc" "$failure_dir/electronic-mail-20200101T000000Z.dump.enc.sha256"

set +e
PATH="$test_path" \
DATABASE_URL='postgresql://backup_user:failure-secret@db.internal/electronic_mail' \
BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
BACKUP_DIR="$failure_dir" \
RETENTION_DAYS=14 \
BACKUP_TEST_LOG="$failure_log" \
BACKUP_TEST_FAIL=1 \
bash "$SCRIPT" > "$TEST_DIR/failure.stdout" 2> "$TEST_DIR/failure.stderr"
failure_status=$?
set -e
[ "$failure_status" -eq 42 ] || fail "pg_dump failure status was not preserved"
[ -f "$failure_dir/electronic-mail-20200101T000000Z.dump.enc" ] \
  || fail "failed backup pruned the last known-good dump"
[ -f "$failure_dir/electronic-mail-20200101T000000Z.dump.enc.sha256" ] \
  || fail "failed backup pruned the last known-good checksum"
if find "$failure_dir" -type f -name '*.partial' -print -quit | grep -q .; then
  fail "partial dump remained after pg_dump failed"
fi
[ ! -d "$failure_dir/.electronic-mail-backup.lock" ] || fail "backup lock remained after failure"

checksum_failure_dir="$TEST_DIR/checksum-failure"
checksum_failure_bin="$TEST_DIR/checksum-failure-bin"
checksum_failure_log="$TEST_DIR/checksum-failure-pg-dump.log"
mkdir -p "$checksum_failure_dir" "$checksum_failure_bin"
ln -s "$(type -P false)" "$checksum_failure_bin/sha256sum"
set +e
PATH="$checksum_failure_bin:$test_path" \
DATABASE_URL='postgresql://backup_user:secret@db.internal/electronic_mail' \
BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
BACKUP_DIR="$checksum_failure_dir" \
RETENTION_DAYS=14 \
BACKUP_TEST_LOG="$checksum_failure_log" \
bash "$SCRIPT" > "$TEST_DIR/checksum-failure.stdout" 2> "$TEST_DIR/checksum-failure.stderr"
checksum_failure_status=$?
set -e
[ "$checksum_failure_status" -ne 0 ] || fail "backup continued after checksum generation failed"
[ -e "$checksum_failure_log" ] || fail "checksum failure test did not reach pg_dump"
if find "$checksum_failure_dir" -type f \
  \( -name 'electronic-mail-*.dump.enc' -o -name 'electronic-mail-*.dump.enc.sha256' -o -name '*.partial' \) \
  -print -quit | grep -q .; then
  fail "checksum failure published an incomplete backup artifact or sidecar"
fi
[ ! -d "$checksum_failure_dir/.electronic-mail-backup.lock" ] \
  || fail "backup lock remained after checksum generation failed"

locked_dir="$TEST_DIR/locked"
mkdir -p "$locked_dir/.electronic-mail-backup.lock"
set +e
PATH="$test_path" \
DATABASE_URL='postgresql://backup_user:secret@db.internal/electronic_mail' \
BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
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

mktemp_failure_dir="$TEST_DIR/mktemp-failure"
mkdir -p "$mktemp_failure_dir"
set +e
PATH="$test_path" \
TMPDIR="$TEST_DIR/missing-tmpdir" \
DATABASE_URL='postgresql://backup_user:secret@db.internal/electronic_mail' \
BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
BACKUP_DIR="$mktemp_failure_dir" \
RETENTION_DAYS=14 \
BACKUP_TEST_LOG="$TEST_DIR/mktemp-failure.log" \
bash "$SCRIPT" > "$TEST_DIR/mktemp-failure.stdout" 2> "$TEST_DIR/mktemp-failure.stderr"
mktemp_failure_status=$?
set -e
[ "$mktemp_failure_status" -ne 0 ] || fail "backup continued after credential temp-directory creation failed"
[ ! -d "$mktemp_failure_dir/.electronic-mail-backup.lock" ] \
  || fail "backup lock remained after credential temp-directory creation failed"
[ ! -e "$TEST_DIR/mktemp-failure.log" ] || fail "pg_dump ran after temp-directory creation failed"

for invalid_retention in 0 014 366 14days; do
  set +e
  PATH="$test_path" \
  DATABASE_URL='postgresql://backup_user:secret@db.internal/electronic_mail' \
  BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
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

set +e
PATH="$test_path" \
DATABASE_URL='postgresql://backup_user:secret@db.internal/electronic_mail' \
BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
BACKUP_DIR="$TEST_DIR/retention-policy-mismatch" \
RETENTION_DAYS=14 \
LEGAL_BACKUP_RETENTION_DAYS=30 \
BACKUP_TEST_LOG="$TEST_DIR/retention-policy-mismatch.log" \
bash "$SCRIPT" > "$TEST_DIR/retention-policy-mismatch.stdout" 2> "$TEST_DIR/retention-policy-mismatch.stderr"
retention_mismatch_status=$?
set -e
[ "$retention_mismatch_status" -ne 0 ] || fail "conflicting legal and operational retention settings were accepted"
grep -Fq 'RETENTION_DAYS must match LEGAL_BACKUP_RETENTION_DAYS when both are set' \
  "$TEST_DIR/retention-policy-mismatch.stderr" \
  || fail "retention policy mismatch did not report the conflict"
[ ! -e "$TEST_DIR/retention-policy-mismatch.log" ] || fail "pg_dump ran with conflicting retention settings"

set +e
PATH="$test_path" \
DATABASE_URL='postgresql://backup_user:secret@db.internal/electronic_mail' \
BACKUP_ENCRYPTION_KEY='not-a-valid-key' \
BACKUP_DIR="$TEST_DIR/invalid-key" \
RETENTION_DAYS=14 \
BACKUP_TEST_LOG="$TEST_DIR/invalid-key.log" \
bash "$SCRIPT" > "$TEST_DIR/invalid-key.stdout" 2> "$TEST_DIR/invalid-key.stderr"
invalid_key_status=$?
set -e
[ "$invalid_key_status" -ne 0 ] || fail "invalid backup encryption key was accepted"
grep -Fq 'BACKUP_ENCRYPTION_KEY must be exactly 64 lowercase hexadecimal characters' "$TEST_DIR/invalid-key.stderr" \
  || fail "invalid backup key did not report the format requirement"
[ ! -e "$TEST_DIR/invalid-key.log" ] || fail "pg_dump ran after backup key validation failed"

for routing_override in \
  'dbname=electronic_mail_shadow' \
  'host=other-db.internal' \
  'service=production'; do
  routing_label="${routing_override%%=*}"
  set +e
  PATH="$test_path" \
  DATABASE_URL="postgresql://backup_user:secret@db.internal/electronic_mail?sslmode=require&$routing_override" \
  BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
  BACKUP_DIR="$TEST_DIR/routing-$routing_label" \
  RETENTION_DAYS=14 \
  BACKUP_TEST_LOG="$TEST_DIR/routing-$routing_label.log" \
  bash "$SCRIPT" > "$TEST_DIR/routing-$routing_label.stdout" 2> "$TEST_DIR/routing-$routing_label.stderr"
  routing_status=$?
  set -e
  [ "$routing_status" -ne 0 ] || fail "$routing_override was allowed to redirect the backup target"
  grep -Fq 'DATABASE_URL connection-routing query parameters are forbidden' "$TEST_DIR/routing-$routing_label.stderr" \
    || fail "$routing_override did not report the routing override error"
  [ ! -e "$TEST_DIR/routing-$routing_label.log" ] || fail "pg_dump ran after $routing_override was rejected"
done

authority_index=0
for unsafe_authority in \
  'isolated.internal,production.internal' \
  'isolated.internal:5432,production.internal:5433' \
  'isolated.internal%2Cproduction.internal' \
  'isolated.internal%3A5432%2Cproduction.internal%3A5433' \
  '%2Fvar%2Frun%2Fpostgresql' \
  '%2Fvar%2Frun%2Fpostgresql:5432'; do
  authority_index=$((authority_index + 1))
  set +e
  PATH="$test_path" \
  DATABASE_URL="postgresql://backup_user:secret@$unsafe_authority/electronic_mail" \
  BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
  BACKUP_DIR="$TEST_DIR/authority-$authority_index" \
  RETENTION_DAYS=14 \
  BACKUP_TEST_LOG="$TEST_DIR/authority-$authority_index.log" \
  bash "$SCRIPT" > "$TEST_DIR/authority-$authority_index.stdout" 2> "$TEST_DIR/authority-$authority_index.stderr"
  authority_status=$?
  set -e
  [ "$authority_status" -ne 0 ] || fail "$unsafe_authority was accepted as one backup host"
  grep -Fq 'DATABASE_URL must identify one explicit host and database' "$TEST_DIR/authority-$authority_index.stderr" \
    || fail "$unsafe_authority did not report the single-host requirement"
  [ ! -e "$TEST_DIR/authority-$authority_index.log" ] || fail "pg_dump ran for unsafe authority $unsafe_authority"
done

echo "backup-postgres authenticated-encryption, security, and retention tests passed"
