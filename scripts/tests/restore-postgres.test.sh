#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="$ROOT_DIR/scripts/restore-postgres.sh"
CRYPTO_SCRIPT="$ROOT_DIR/scripts/postgres-backup-crypto.py"
STUB="$ROOT_DIR/scripts/tests/fixtures/restore-pg-restore-stub.sh"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/electronic-mail-restore-test.XXXXXX")"
trap 'rm -rf "$TEST_DIR"' EXIT
BACKUP_KEY=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
WRONG_KEY=abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789

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

expect_failure() {
  local label="$1"
  local expected_message="$2"
  shift 2
  local status
  set +e
  "$@" > "$TEST_DIR/$label.stdout" 2> "$TEST_DIR/$label.stderr"
  status=$?
  set -e
  [ "$status" -ne 0 ] || fail "$label unexpectedly passed"
  grep -Fq "$expected_message" "$TEST_DIR/$label.stderr" || {
    sed -n '1,120p' "$TEST_DIR/$label.stderr" >&2
    fail "$label did not report: $expected_message"
  }
}

fake_bin="$TEST_DIR/bin"
mkdir -p "$fake_bin"
ln -s "$STUB" "$fake_bin/pg_restore"
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
success_restore_database_url='postgresql://restore_user:s3cr%3At@db.internal:5433/electronic_mail_restore?sslmode=require'

key_file="$TEST_DIR/encryption-key"
plaintext_source="$TEST_DIR/source.dump"
backup_file="$TEST_DIR/electronic-mail-test.dump.enc"
printf '%s\n' "$BACKUP_KEY" > "$key_file"
chmod 600 "$key_file"

create_encrypted_backup() {
  rm -f "$plaintext_source" "$backup_file" "$backup_file.sha256"
  printf 'verified backup data\n' > "$plaintext_source"
  "$ROOT_DIR/.venv/bin/python" "$CRYPTO_SCRIPT" encrypt \
    --key-file "$key_file" --input "$plaintext_source" --output "$backup_file"
  rm -f "$plaintext_source"
  checksum_file "$backup_file" > "$backup_file.sha256"
}

run_isolated_restore() {
  local log_path="$1"
  shift
  env \
    PATH="$test_path" \
    CONFIRM_RESTORE=RESTORE \
    RESTORE_DATABASE_URL="$success_restore_database_url" \
    EXPECTED_RESTORE_DATABASE=electronic_mail_restore \
    BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
    BACKUP_FILE="$backup_file" \
    RESTORE_TEST_LOG="$log_path" \
    raw_restore_database_url=preexported-raw-url backup_encryption_key=preexported-key \
    PGHOST=poison-host PGHOSTADDR=203.0.113.10 PGPORT=9999 PGUSER=poison-user \
    PGDATABASE=poison-database PGPASSWORD=poison-password PGPASSFILE="$TEST_DIR/poison-pgpass" \
    PGSERVICE=poison-service PGSERVICEFILE="$TEST_DIR/poison-service.conf" \
    "$@" \
    bash "$SCRIPT"
}

create_encrypted_backup
success_log="$TEST_DIR/success-pg-restore.log"
run_isolated_restore "$success_log" > "$TEST_DIR/success.stdout"

grep -Fxq 'DATABASE_URL=' "$success_log" || fail "pg_restore inherited DATABASE_URL"
grep -Fxq 'RESTORE_DATABASE_URL=' "$success_log" || fail "pg_restore inherited RESTORE_DATABASE_URL"
grep -Fxq 'BACKUP_ENCRYPTION_KEY=' "$success_log" || fail "pg_restore inherited BACKUP_ENCRYPTION_KEY"
grep -Fxq 'PGPASSWORD=' "$success_log" || fail "pg_restore inherited PGPASSWORD"
grep -Fxq 'PGDATABASE=postgresql://restore_user@db.internal:5433/electronic_mail_restore?sslmode=require' "$success_log" \
  || fail "pg_restore did not receive the sanitized connection URL"
grep -Fxq 'PGPASS=*:*:*:*:s3cr\:t' "$success_log" \
  || fail "PGPASSFILE did not contain the decoded escaped password"
grep -Fxq 'PGPASSMODE=600' "$success_log" || fail "PGPASSFILE permissions were not 0600"
for cleared_routing_variable in PGHOST PGHOSTADDR PGPORT PGUSER PGSERVICE PGSERVICEFILE; do
  grep -Fxq "$cleared_routing_variable=" "$success_log" \
    || fail "pg_restore inherited $cleared_routing_variable"
done
grep -Fxq 'INPUT_SOURCE=stdin' "$success_log" || fail "pg_restore did not read the dump from stdin"
grep -Fxq 'INPUT_CONTENT=verified backup data' "$success_log" || fail "pg_restore did not receive the authenticated plaintext"
for required_argument in --clean --if-exists --no-owner --no-acl --exit-on-error --single-transaction; do
  grep -Fxq "ARG=$required_argument" "$success_log" || fail "missing pg_restore argument $required_argument"
done
if grep -Eq 'ARG=.*(--dbname|s3cr|postgresql://|electronic-mail-test\.dump\.enc)' "$success_log"; then
  fail "pg_restore arguments exposed connection details or the encrypted artifact instead of plaintext"
fi
pgpass_path="$(awk -F= '$1 == "PGPASSFILE" { print substr($0, index($0, "=") + 1) }' "$success_log")"
[ -n "$pgpass_path" ] && [ ! -e "$pgpass_path" ] || fail "temporary PGPASSFILE was not removed"
if grep -Eq '^ARG=[^-]' "$success_log"; then
  fail "pg_restore received a plaintext archive path instead of stdin"
fi
if find "$TEST_DIR" -type f -name 'restore.dump' -print -quit | grep -q .; then
  fail "restore wrote plaintext to a temporary dump"
fi

empty_child_secrets='DATABASE_URL=|RESTORE_DATABASE_URL=|BACKUP_ENCRYPTION_KEY=|PGDATABASE=|PGPASSWORD=|PGPASSFILE=|raw_database_url=|raw_restore_database_url=|backup_encryption_key='
for early_child in dirname mktemp; do
  grep -Fxq "CHILD=$early_child|$empty_child_secrets" "$secret_lifetime_log" \
    || fail "$early_child inherited a captured restore secret"
done
grep -Fxq "CHILD=validate-key|DATABASE_URL=|RESTORE_DATABASE_URL=|BACKUP_ENCRYPTION_KEY=$BACKUP_KEY|PGDATABASE=|PGPASSWORD=|PGPASSFILE=|raw_database_url=|raw_restore_database_url=|backup_encryption_key=" \
  "$secret_lifetime_log" || fail "key validation inherited the restore URL or missed its scoped key"
grep -Fxq "CHILD=sanitize|DATABASE_URL=|RESTORE_DATABASE_URL=$success_restore_database_url|BACKUP_ENCRYPTION_KEY=|PGDATABASE=|PGPASSWORD=|PGPASSFILE=|raw_database_url=|raw_restore_database_url=|backup_encryption_key=" \
  "$secret_lifetime_log" || fail "URL sanitizer inherited the encryption key or missed its scoped URL"
grep -Fxq "CHILD=decrypt-stdout|DATABASE_URL=|RESTORE_DATABASE_URL=|BACKUP_ENCRYPTION_KEY=$BACKUP_KEY|PGDATABASE=|PGPASSWORD=|PGPASSFILE=|raw_database_url=|raw_restore_database_url=|backup_encryption_key=" \
  "$secret_lifetime_log" || fail "decryption inherited the restore URL or missed its scoped key"
if grep -Eq '\|(raw_database_url|raw_restore_database_url|backup_encryption_key)=[^|]' "$secret_lifetime_log"; then
  fail "captured restore secrets remained exported to child processes"
fi

xtrace_restore_password=xtrace-restore-password-sentinel
xtrace_restore_log="$TEST_DIR/xtrace-pg-restore.log"
env \
  PATH="$test_path" \
  CONFIRM_RESTORE=RESTORE \
  RESTORE_DATABASE_URL="postgresql://restore_user:$xtrace_restore_password@db.internal/electronic_mail_restore" \
  EXPECTED_RESTORE_DATABASE=electronic_mail_restore \
  BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
  BACKUP_FILE="$backup_file" \
  RESTORE_TEST_LOG="$xtrace_restore_log" \
  bash -x "$SCRIPT" > "$TEST_DIR/xtrace.stdout" 2> "$TEST_DIR/xtrace.stderr"
grep -Fq 'set +x' "$TEST_DIR/xtrace.stderr" || fail "xtrace regression did not start with tracing enabled"
if grep -Fq "$xtrace_restore_password" "$TEST_DIR/xtrace.stderr"; then
  fail "bash -x exposed the restore URL password"
fi
if grep -Fq "$BACKUP_KEY" "$TEST_DIR/xtrace.stderr"; then
  fail "bash -x exposed the restore encryption key"
fi
grep -Fxq 'INPUT_CONTENT=verified backup data' "$xtrace_restore_log" \
  || fail "xtrace-protected restore did not complete successfully"
grep -Fxq 'BACKUP_ENCRYPTION_KEY=' "$xtrace_restore_log" \
  || fail "xtrace-protected restore leaked the key to pg_restore"

stable_snapshot_backup="$TEST_DIR/electronic-mail-stable-snapshot.dump.enc"
stable_snapshot_output="$TEST_DIR/stable-snapshot.out"
cp "$backup_file" "$stable_snapshot_backup"
stable_snapshot_checksum="$(checksum_file "$stable_snapshot_backup" | awk '{ print $1 }')"
"$ROOT_DIR/.venv/bin/python" - "$CRYPTO_SCRIPT" "$stable_snapshot_backup" \
  "$BACKUP_KEY" "$stable_snapshot_checksum" > "$stable_snapshot_output" <<'PY'
import importlib.util
from pathlib import Path
import sys

helper_path = Path(sys.argv[1])
input_path = Path(sys.argv[2])
key = bytes.fromhex(sys.argv[3])
expected_checksum = sys.argv[4]
spec = importlib.util.spec_from_file_location("postgres_backup_crypto", helper_path)
assert spec is not None and spec.loader is not None
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

original_decrypt_pass = helper._decrypt_pass
mutated = False

def mutate_source_after_authentication(source, **arguments):
    global mutated
    result = original_decrypt_pass(source, **arguments)
    if arguments["destination"] is None and not mutated:
        input_path.write_bytes(b"mutated after authentication\n")
        mutated = True
    return result

helper._decrypt_pass = mutate_source_after_authentication
helper.decrypt_stdout(input_path, key, expected_checksum)
assert mutated
PY
grep -Fxq 'verified backup data' "$stable_snapshot_output" \
  || fail "decrypt-stdout reread a mutable input instead of its authenticated ciphertext snapshot"

failure_log="$TEST_DIR/failure-pg-restore.log"
set +e
run_isolated_restore "$failure_log" RESTORE_TEST_FAIL=1 > "$TEST_DIR/failure.stdout" 2> "$TEST_DIR/failure.stderr"
failure_status=$?
set -e
[ "$failure_status" -eq 43 ] || fail "pg_restore failure status was not preserved"
failure_pgpass_path="$(awk -F= '$1 == "PGPASSFILE" { print substr($0, index($0, "=") + 1) }' "$failure_log")"
[ -n "$failure_pgpass_path" ] && [ ! -e "$failure_pgpass_path" ] \
  || fail "temporary credentials remained after pg_restore failed"
if find "$TEST_DIR" -type f -name 'restore.dump' -print -quit | grep -q .; then
  fail "pg_restore failure left a plaintext dump behind"
fi

printf 'transport tamper\n' >> "$backup_file"
expect_failure checksum-tamper 'Backup checksum verification failed' \
  run_isolated_restore "$TEST_DIR/checksum-tamper.log"
[ ! -e "$TEST_DIR/checksum-tamper.log" ] || fail "pg_restore ran after checksum failure"

checksum_file "$backup_file" > "$backup_file.sha256"
expect_failure authenticated-tamper 'encrypted backup authentication failed' \
  run_isolated_restore "$TEST_DIR/authenticated-tamper.log"
[ ! -e "$TEST_DIR/authenticated-tamper.log" ] || fail "pg_restore ran after authenticated decryption failed"
if find "$TEST_DIR" -type f -name 'restore.dump' -print -quit | grep -q .; then
  fail "failed authentication left plaintext behind"
fi

create_encrypted_backup
expect_failure wrong-key 'encrypted backup authentication failed' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE \
    RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal/electronic_mail_restore' \
    EXPECTED_RESTORE_DATABASE=electronic_mail_restore BACKUP_ENCRYPTION_KEY="$WRONG_KEY" \
    BACKUP_FILE="$backup_file" RESTORE_TEST_LOG="$TEST_DIR/wrong-key.log" bash "$SCRIPT"
[ ! -e "$TEST_DIR/wrong-key.log" ] || fail "pg_restore ran with the wrong encryption key"

expect_failure wrong-target 'Restore database mismatch' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE \
    RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal/electronic_mail_restore' \
    EXPECTED_RESTORE_DATABASE=wrong_database BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
    BACKUP_FILE="$backup_file" RESTORE_TEST_LOG="$TEST_DIR/wrong-target.log" bash "$SCRIPT"
[ ! -e "$TEST_DIR/wrong-target.log" ] || fail "pg_restore ran for a mismatched target"

expect_failure protected-target 'Refusing to treat protected database electronic_mail as an isolated restore target' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE \
    RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal/electronic_mail' \
    EXPECTED_RESTORE_DATABASE=electronic_mail BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
    BACKUP_FILE="$backup_file" RESTORE_TEST_LOG="$TEST_DIR/protected.log" bash "$SCRIPT"
[ ! -e "$TEST_DIR/protected.log" ] || fail "pg_restore ran against a protected isolated target"

for routing_override in \
  'dbname=electronic_mail' \
  'host=production-db.internal' \
  'service=production'; do
  routing_label="${routing_override%%=*}"
  expect_failure "routing-$routing_label" 'RESTORE_DATABASE_URL connection-routing query parameters are forbidden' \
    env PATH="$test_path" CONFIRM_RESTORE=RESTORE \
      RESTORE_DATABASE_URL="postgresql://restore_user:secret@db.internal/electronic_mail_restore?sslmode=require&$routing_override" \
      EXPECTED_RESTORE_DATABASE=electronic_mail_restore BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
      BACKUP_FILE="$backup_file" RESTORE_TEST_LOG="$TEST_DIR/routing-$routing_label.log" bash "$SCRIPT"
  [ ! -e "$TEST_DIR/routing-$routing_label.log" ] || fail "pg_restore ran after $routing_override was rejected"
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
  expect_failure "authority-$authority_index" 'RESTORE_DATABASE_URL must identify one explicit host and database' \
    env PATH="$test_path" CONFIRM_RESTORE=RESTORE \
      RESTORE_DATABASE_URL="postgresql://restore_user:secret@$unsafe_authority/electronic_mail_restore" \
      EXPECTED_RESTORE_DATABASE=electronic_mail_restore BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
      BACKUP_FILE="$backup_file" RESTORE_TEST_LOG="$TEST_DIR/authority-$authority_index.log" bash "$SCRIPT"
  [ ! -e "$TEST_DIR/authority-$authority_index.log" ] || fail "pg_restore ran for unsafe authority $unsafe_authority"
done

rm -f "$backup_file.sha256"
unverified_log="$TEST_DIR/unverified-isolated.log"
run_isolated_restore "$unverified_log" ALLOW_UNVERIFIED_RESTORE=1 > "$TEST_DIR/unverified-isolated.stdout"
grep -Fxq 'INPUT_CONTENT=verified backup data' "$unverified_log" \
  || fail "reviewed isolated restore did not retain authenticated decryption"

expect_failure production-unverified 'ALLOW_UNVERIFIED_RESTORE is forbidden for production restores' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE CONFIRM_PRODUCTION_RESTORE=RESTORE_PRODUCTION_DATABASE \
    RESTORE_TARGET_CLASS=production ALLOW_UNVERIFIED_RESTORE=1 \
    RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal/electronic_mail' \
    EXPECTED_RESTORE_DATABASE=electronic_mail BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
    BACKUP_FILE="$backup_file" RESTORE_TEST_LOG="$TEST_DIR/production-unverified.log" bash "$SCRIPT"
[ ! -e "$TEST_DIR/production-unverified.log" ] || fail "pg_restore ran for an unverified production restore"

plaintext_legacy="$TEST_DIR/electronic-mail-legacy.dump"
printf 'legacy plaintext\n' > "$plaintext_legacy"
expect_failure plaintext-artifact 'authenticated encrypted .dump.enc artifact' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE \
    RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal/electronic_mail_restore' \
    EXPECTED_RESTORE_DATABASE=electronic_mail_restore BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
    BACKUP_FILE="$plaintext_legacy" RESTORE_TEST_LOG="$TEST_DIR/plaintext.log" bash "$SCRIPT"

echo "restore-postgres authenticated-encryption, credential, target, and checksum tests passed"
