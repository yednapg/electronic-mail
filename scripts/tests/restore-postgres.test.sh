#!/usr/bin/env bash
set -euo pipefail

if [ "${0##*/}" = "psql" ]; then
  if [ "${1:-}" = "--version" ]; then
    printf 'psql (PostgreSQL) %s\n' "${RESTORE_TEST_PSQL_VERSION:-17.10}"
    exit 0
  fi
  : "${RESTORE_TEST_PSQL_LOG:?RESTORE_TEST_PSQL_LOG is required}"
  : "${RESTORE_TEST_EVENT_LOG:?RESTORE_TEST_EVENT_LOG is required}"
  command_text="$*"
  connection_descriptor="${PGDATABASE:-} $command_text "
  maintenance_call=0
  case "$connection_descriptor" in
    *'/postgres?'* | *'/postgres '*) maintenance_call=1 ;;
  esac
  {
    if [ "$maintenance_call" = "1" ]; then
      printf 'CALL=psql-maintenance\n'
    else
      printf 'CALL=psql-target\n'
    fi
    printf 'DATABASE_URL=%s\n' "${DATABASE_URL:-}"
    printf 'RESTORE_DATABASE_URL=%s\n' "${RESTORE_DATABASE_URL:-}"
    printf 'RESTORE_MAINTENANCE_DATABASE_URL=%s\n' "${RESTORE_MAINTENANCE_DATABASE_URL:-}"
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
    printf 'PGSSLMODE=%s\n' "${PGSSLMODE:-}"
    printf 'PGSSLROOTCERT=%s\n' "${PGSSLROOTCERT:-}"
    printf 'PGSSLPASSWORD=%s\n' "${PGSSLPASSWORD:-}"
    printf 'PGGSSENCMODE=%s\n' "${PGGSSENCMODE:-}"
    printf 'PGAPPNAME=%s\n' "${PGAPPNAME:-}"
    printf 'SSL_CERT_FILE=%s\n' "${SSL_CERT_FILE:-}"
    printf 'SSL_CERT_DIR=%s\n' "${SSL_CERT_DIR:-}"
    printf 'PGCONNECT_TIMEOUT=%s\n' "${PGCONNECT_TIMEOUT:-}"
    printf 'PGOPTIONS=%s\n' "${PGOPTIONS:-}"
    if [ -n "${PGPASSFILE:-}" ] && [ -f "$PGPASSFILE" ]; then
      printf 'PGPASS=%s\n' "$(tr -d '\n' < "$PGPASSFILE")"
      printf 'PGPASSMODE=%s\n' "$(stat -f '%Lp' "$PGPASSFILE" 2>/dev/null || stat -c '%a' "$PGPASSFILE")"
    fi
    for argument in "$@"; do
      printf 'ARG=%s\n' "$argument"
    done
    printf 'END=psql\n'
  } >> "$RESTORE_TEST_PSQL_LOG"

  if [ "$maintenance_call" = "1" ]; then
      maintenance_closed=0
      close_maintenance_stub() {
        if [ "$maintenance_closed" = "0" ]; then
          printf 'MAINTENANCE_SESSION_CLOSED\n' >> "$RESTORE_TEST_EVENT_LOG"
          maintenance_closed=1
        fi
      }
      trap close_maintenance_stub EXIT
      trap 'exit 143' HUP INT TERM
      printf 'MAINTENANCE_SESSION_CONNECTED\n' >> "$RESTORE_TEST_EVENT_LOG"
      maintenance_statement=""
      while IFS= read -r maintenance_line; do
        maintenance_statement="${maintenance_statement}${maintenance_statement:+
}${maintenance_line}"
        case "$maintenance_line" in
          *\;)
            if [[ "$maintenance_statement" == *MAINTENANCE_IDENTITY_OK* ]]; then
              case "${RESTORE_TEST_MAINTENANCE_IDENTITY_STATE:-MATCH}" in
                MATCH)
                  printf 'MAINTENANCE_IDENTITY_OK\n'
                  printf 'MAINTENANCE_IDENTIFIED_TARGET\n' >> "$RESTORE_TEST_EVENT_LOG"
                  ;;
                WRONG_CLUSTER | MISSING_TARGET)
                  printf 'MAINTENANCE_IDENTITY_MISMATCH\n'
                  printf 'MAINTENANCE_IDENTITY_REJECTED_%s\n' \
                    "$RESTORE_TEST_MAINTENANCE_IDENTITY_STATE" >> "$RESTORE_TEST_EVENT_LOG"
                  ;;
              esac
            elif [[ "$maintenance_statement" == *'ALLOW_CONNECTIONS false'* ]]; then
              printf 'CONNECTIONS_DISABLE_ATTEMPTED\n' >> "$RESTORE_TEST_EVENT_LOG"
              printf 'CONNECTIONS_DISABLED\n' >> "$RESTORE_TEST_EVENT_LOG"
              if [ "${RESTORE_TEST_MAINTENANCE_LOSS_AFTER_DISABLE:-0}" = "1" ]; then
                exit 48
              fi
            elif [[ "$maintenance_statement" == *MAINTENANCE_DISABLE_OK* ]]; then
              printf 'MAINTENANCE_DISABLE_OK\n'
            elif [[ "$maintenance_statement" == *MAINTENANCE_TERMINATION_IDENTITY_OK* ]]; then
              printf 'MAINTENANCE_TERMINATION_IDENTITY_OK\n'
              printf 'MAINTENANCE_RECONFIRMED_TARGET\n' >> "$RESTORE_TEST_EVENT_LOG"
            elif [[ "$maintenance_statement" == *MAINTENANCE_TERMINATION_DONE* ]]; then
              printf 'TERMINATION_ATTEMPTED\n' >> "$RESTORE_TEST_EVENT_LOG"
              printf 'MAINTENANCE_TERMINATION_DONE\n'
            elif [[ "$maintenance_statement" == *MAINTENANCE_ISOLATION_OK* ]]; then
              if [ "${RESTORE_TEST_ISOLATION_FAIL:-0}" = "1" ]; then
                printf 'MAINTENANCE_ISOLATION_FAILED\n'
                printf 'TARGET_ISOLATION_REJECTED\n' >> "$RESTORE_TEST_EVENT_LOG"
              else
                printf 'MAINTENANCE_ISOLATION_OK\n'
                printf 'SOLE_TARGET_BACKEND_CONFIRMED\n' >> "$RESTORE_TEST_EVENT_LOG"
              fi
            elif [[ "$maintenance_statement" == *'ALLOW_CONNECTIONS true'* ]]; then
              printf 'CONNECTIONS_REENABLE_ATTEMPTED\n' >> "$RESTORE_TEST_EVENT_LOG"
            elif [[ "$maintenance_statement" == *MAINTENANCE_REENABLE_OK* ]]; then
              if [ "${RESTORE_TEST_REENABLE_FAIL:-0}" = "1" ]; then
                printf 'MAINTENANCE_REENABLE_FAILED\n'
                printf 'CONNECTIONS_REENABLE_FAILED\n' >> "$RESTORE_TEST_EVENT_LOG"
              else
                printf 'MAINTENANCE_REENABLE_OK\n'
                printf 'CONNECTIONS_ENABLED\n' >> "$RESTORE_TEST_EVENT_LOG"
              fi
            fi
            maintenance_statement=""
            ;;
        esac
      done
      exit 0
  fi

  transaction_started=0
  transaction_committed=0
  transaction_rollback_logged=0
  target_application_name=""
  log_target_rollback() {
    if [ "$transaction_started" = "1" ] \
      && [ "$transaction_committed" = "0" ] \
      && [ "$transaction_rollback_logged" = "0" ]; then
      printf 'TARGET_TRANSACTION_ROLLED_BACK\n' >> "$RESTORE_TEST_EVENT_LOG"
      transaction_rollback_logged=1
    fi
  }
  finish_target_session() {
    local status=$?
    log_target_rollback
    return "$status"
  }
  trap finish_target_session EXIT
  trap 'exit 143' HUP INT TERM

  printf 'TARGET_SESSION_CONNECTED\n' >> "$RESTORE_TEST_EVENT_LOG"
  while IFS= read -r sql_line; do
    case "$sql_line" in
      *"SET application_name = '"*)
        target_application_name="${sql_line#*\'}"
        target_application_name="${target_application_name%%\'*}"
        ;;
      BEGIN\;)
        transaction_started=1
        printf 'TARGET_TRANSACTION_STARTED\n' >> "$RESTORE_TEST_EVENT_LOG"
        ;;
      COMMIT\;)
        transaction_committed=1
        printf 'TARGET_TRANSACTION_COMMITTED\n' >> "$RESTORE_TEST_EVENT_LOG"
        ;;
      *"SELECT 'RESTORE_PRECOMMIT_OK_"*)
        precommit_marker="${sql_line#*\'}"
        precommit_marker="${precommit_marker%%\'*}"
        status_nonce="${precommit_marker#RESTORE_PRECOMMIT_OK_}"
        if [[ "$target_application_name" == *"$status_nonce"* ]]; then
          printf 'STATUS_NONCE_REUSED_IN_APPLICATION_NAME\n' >> "$RESTORE_TEST_EVENT_LOG"
        else
          printf 'STATUS_NONCE_INDEPENDENT\n' >> "$RESTORE_TEST_EVENT_LOG"
        fi
        printf '%s\n' "$precommit_marker"
        printf 'TARGET_PRECOMMIT_ACKNOWLEDGED\n' >> "$RESTORE_TEST_EVENT_LOG"
        ;;
      *"SELECT 'RESTORE_COMMIT_OK_"*)
        commit_marker="${sql_line#*\'}"
        commit_marker="${commit_marker%%\'*}"
        if [ "${RESTORE_TEST_SUPPRESS_COMMIT_ACK:-0}" = "1" ]; then
          printf 'TARGET_COMMIT_ACK_SUPPRESSED\n' >> "$RESTORE_TEST_EVENT_LOG"
          exit 49
        else
          printf '%s\n' "$commit_marker"
          printf 'TARGET_COMMIT_ACKNOWLEDGED\n' >> "$RESTORE_TEST_EVENT_LOG"
          if [ -n "${RESTORE_TEST_TARGET_EXIT_AFTER_ACK:-}" ]; then
            exit "$RESTORE_TEST_TARGET_EXIT_AFTER_ACK"
          fi
        fi
        ;;
      *pg_try_advisory_lock*)
        if [ "${RESTORE_TEST_LOCK_UNAVAILABLE:-0}" = "1" ]; then
          printf 'RESTORE_LOCK_UNAVAILABLE\n'
          printf 'RESTORE_LOCK_REJECTED\n' >> "$RESTORE_TEST_EVENT_LOG"
          continue
        fi
        printf 'TARGET_PID=4242\n'
        printf 'TARGET_BACKEND_PID=4242\n' >> "$RESTORE_TEST_EVENT_LOG"
        printf 'RESTORE_LOCK_ACQUIRED\n' >> "$RESTORE_TEST_EVENT_LOG"
        ;;
      *"SELECT 'TARGET_TRANSACTION_READY'"*)
        printf 'TARGET_TRANSACTION_READY\n'
        ;;
      *"SELECT 'RESTORE_PREFLIGHT_OK'"*)
        if [ "${RESTORE_TEST_PREFLIGHT_FAIL:-0}" = "1" ]; then
          printf 'ERROR: simulated preflight execution failure\n' >&2
          printf 'PRISTINE_PREFLIGHT_FAILED\n' >> "$RESTORE_TEST_EVENT_LOG"
          exit 45
        fi
        case "${RESTORE_TEST_TARGET_STATE:-EMPTY}" in
          EMPTY)
            printf 'RESTORE_PREFLIGHT_OK\n'
            printf 'PRISTINE_PREFLIGHT_PASSED\n' >> "$RESTORE_TEST_EVENT_LOG"
            ;;
          NONEMPTY | HOSTILE_METADATA)
            printf '%s\n' \
              'ERROR: Refusing restore: target database is not empty or its public schema metadata is not pristine' >&2
            printf 'PRISTINE_PREFLIGHT_REJECTED\n' >> "$RESTORE_TEST_EVENT_LOG"
            exit 45
            ;;
          *)
            printf 'ERROR: preflight returned an unexpected result\n' >&2
            printf 'PRISTINE_PREFLIGHT_FAILED\n' >> "$RESTORE_TEST_EVENT_LOG"
            exit 45
            ;;
        esac
        ;;
      *RESTORE_STUB_APPLIED*)
        printf 'RESTORE_SQL_IN_TARGET_SESSION\n' >> "$RESTORE_TEST_EVENT_LOG"
        if [ "${RESTORE_TEST_PSQL_FAIL:-0}" = "1" ]; then
          exit 44
        fi
        ;;
    esac
  done
  exit 0
fi

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

maintenance_call_count() {
  awk '$0 == "CALL=psql-maintenance" { count += 1 } END { print count + 0 }' \
    "$psql_log" 2>/dev/null || printf '0\n'
}

grep -Fq "server_version_num')::pg_catalog.int4 < 170010" "$SCRIPT" \
  || fail "restore server floor does not encode PostgreSQL 17.10 as 170010"

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
ln -s "$ROOT_DIR/scripts/tests/restore-postgres.test.sh" "$fake_bin/psql"
ln -s "$STUB" "$fake_bin/dirname"
ln -s "$STUB" "$fake_bin/mkdir"
ln -s "$STUB" "$fake_bin/mktemp"
ln -s "$STUB" "$fake_bin/python-wrapper"
test_path="$fake_bin:$PATH"
secret_lifetime_log="$TEST_DIR/secret-lifetime.log"
psql_log="$TEST_DIR/psql.log"
event_log="$TEST_DIR/restore-events.log"
export SECRET_LIFETIME_TEST_LOG="$secret_lifetime_log"
export RESTORE_TEST_PSQL_LOG="$psql_log"
export RESTORE_TEST_EVENT_LOG="$event_log"
export SECRET_TEST_REAL_DIRNAME="$(type -P dirname)"
export SECRET_TEST_REAL_MKDIR="$(type -P mkdir)"
export SECRET_TEST_REAL_MKTEMP="$(type -P mktemp)"
export SECRET_TEST_REAL_PYTHON="$ROOT_DIR/.venv/bin/python"
export BACKUP_CRYPTO_PYTHON="$fake_bin/python-wrapper"
export CONFIRM_DIRECT_RESTORE_ENDPOINTS=DIRECT_SINGLE_CLUSTER
success_restore_database_url='postgresql://restore_user:s3cr%3At@db.internal:5433/electronic_mail_restore?sslmode=require'
success_maintenance_database_url='postgresql://maintenance_user:maint%3At@db.internal:5433/postgres?sslmode=verify-full&sslrootcert=system&gssencmode=disable'
default_maintenance_database_url='postgresql://maintenance_user:maint%3At@db.internal/postgres?sslmode=verify-full&sslrootcert=system&gssencmode=disable'
export RESTORE_MAINTENANCE_DATABASE_URL="$default_maintenance_database_url"

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
    DATABASE_URL='postgresql://source_user:poison-source-secret@source-db.internal/electronic_mail' \
    CONFIRM_RESTORE=RESTORE \
    RESTORE_DATABASE_URL="$success_restore_database_url" \
    RESTORE_MAINTENANCE_DATABASE_URL="$success_maintenance_database_url" \
    EXPECTED_RESTORE_DATABASE=electronic_mail_restore \
    BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
    BACKUP_FILE="$backup_file" \
    RESTORE_TEST_LOG="$log_path" \
    raw_restore_database_url=preexported-raw-url backup_encryption_key=preexported-key \
    PGHOST=poison-host PGHOSTADDR=203.0.113.10 PGPORT=9999 PGUSER=poison-user \
    PGDATABASE=poison-database PGPASSWORD=poison-password PGPASSFILE="$TEST_DIR/poison-pgpass" \
    PGSERVICE=poison-service PGSERVICEFILE="$TEST_DIR/poison-service.conf" \
    PGSSLMODE=disable PGSSLROOTCERT="$TEST_DIR/poison-root.crt" \
    PGSSLPASSWORD=poison-tls-private-key-password PGGSSENCMODE=require \
    SSL_CERT_FILE="$TEST_DIR/poison-openssl-root.crt" SSL_CERT_DIR="$TEST_DIR/poison-openssl-dir" \
    "$@" \
    bash "$SCRIPT"
}

create_encrypted_backup
expect_failure missing-direct-endpoint-confirmation \
  'Set CONFIRM_DIRECT_RESTORE_ENDPOINTS=DIRECT_SINGLE_CLUSTER' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE CONFIRM_DIRECT_RESTORE_ENDPOINTS= \
    bash "$SCRIPT"
[ "$(grep -Fc 'read -r -t "$RESTORE_FINALIZE_TIMEOUT_SECONDS"' "$SCRIPT")" -eq 2 ] \
  || fail "validated restore finalize timeout is not wired to both transaction sentinels"
for invalid_finalize_timeout in 59 999999999999999999999999999999; do
  expect_failure "invalid-finalize-timeout-$invalid_finalize_timeout" \
    'RESTORE_FINALIZE_TIMEOUT_SECONDS must be an integer from 60 through 86400' \
    env PATH="$test_path" CONFIRM_RESTORE=RESTORE \
      RESTORE_FINALIZE_TIMEOUT_SECONDS="$invalid_finalize_timeout" \
      RESTORE_DATABASE_URL="$success_restore_database_url" \
      RESTORE_MAINTENANCE_DATABASE_URL="$success_maintenance_database_url" \
      EXPECTED_RESTORE_DATABASE=electronic_mail_restore BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
      bash "$SCRIPT"
done

success_log="$TEST_DIR/success-pg-restore.log"
run_isolated_restore "$success_log" > "$TEST_DIR/success.stdout"

grep -Fxq 'DATABASE_URL=' "$success_log" || fail "pg_restore inherited DATABASE_URL"
grep -Fxq 'RESTORE_DATABASE_URL=' "$success_log" || fail "pg_restore inherited RESTORE_DATABASE_URL"
grep -Fxq 'RESTORE_MAINTENANCE_DATABASE_URL=' "$success_log" \
  || fail "pg_restore inherited RESTORE_MAINTENANCE_DATABASE_URL"
grep -Fxq 'BACKUP_ENCRYPTION_KEY=' "$success_log" || fail "pg_restore inherited BACKUP_ENCRYPTION_KEY"
grep -Fxq 'PGPASSWORD=' "$success_log" || fail "pg_restore inherited PGPASSWORD"
grep -Fxq 'PGDATABASE=' "$success_log" || fail "SQL-only pg_restore inherited a database target"
grep -Fxq 'PGPASSFILE=' "$success_log" || fail "SQL-only pg_restore inherited target credentials"
for cleared_routing_variable in PGHOST PGHOSTADDR PGPORT PGUSER PGSERVICE PGSERVICEFILE PGSSLMODE PGSSLROOTCERT PGSSLPASSWORD PGGSSENCMODE SSL_CERT_FILE SSL_CERT_DIR; do
  grep -Fxq "$cleared_routing_variable=" "$success_log" \
    || fail "pg_restore inherited $cleared_routing_variable"
done
grep -Fxq 'INPUT_SOURCE=stdin' "$success_log" || fail "pg_restore did not read the dump from stdin"
grep -Fxq 'INPUT_CONTENT=verified backup data' "$success_log" || fail "pg_restore did not receive the authenticated plaintext"
for required_argument in --clean --if-exists --no-owner --no-acl --exit-on-error --file=-; do
  grep -Fxq "ARG=$required_argument" "$success_log" || fail "missing pg_restore argument $required_argument"
done
if grep -Fxq 'ARG=--single-transaction' "$success_log"; then
  fail "pg_restore opened its own transaction instead of generating SQL for the isolated target session"
fi
grep -Fxq 'CALL=psql-target' "$psql_log" || fail "atomic target psql session did not run"
grep -Fxq 'CALL=psql-maintenance' "$psql_log" || fail "maintenance isolation psql did not run"
grep -Fxq 'DATABASE_URL=' "$psql_log" || fail "psql inherited DATABASE_URL"
grep -Fxq 'RESTORE_DATABASE_URL=' "$psql_log" || fail "psql inherited RESTORE_DATABASE_URL"
grep -Fxq 'RESTORE_MAINTENANCE_DATABASE_URL=' "$psql_log" \
  || fail "psql inherited raw RESTORE_MAINTENANCE_DATABASE_URL"
grep -Fxq 'BACKUP_ENCRYPTION_KEY=' "$psql_log" || fail "psql inherited BACKUP_ENCRYPTION_KEY"
grep -Fxq 'PGPASSWORD=' "$psql_log" || fail "psql inherited PGPASSWORD"
grep -Fxq 'ARG=--dbname=postgresql://restore_user@db.internal:5433/electronic_mail_restore?sslmode=require' "$psql_log" \
  || fail "psql preflight did not inspect the sanitized target URL"
grep -Fxq 'PGPASS=*:*:*:*:s3cr\:t' "$psql_log" \
  || fail "atomic target psql did not use the decoded escaped password"
grep -Fxq 'PGPASS=*:*:*:*:maint\:t' "$psql_log" \
  || fail "maintenance psql did not use its separately scoped decoded password"
grep -Fxq 'PGPASSMODE=600' "$psql_log" || fail "psql PGPASSFILE permissions were not 0600"
for cleared_routing_variable in PGHOST PGHOSTADDR PGPORT PGUSER PGSERVICE PGSERVICEFILE PGSSLMODE PGSSLROOTCERT PGSSLPASSWORD PGGSSENCMODE SSL_CERT_FILE SSL_CERT_DIR; do
  grep -Fxq "$cleared_routing_variable=" "$psql_log" \
    || fail "psql preflight inherited $cleared_routing_variable"
done

expect_failure tls-key-password-in-url 'RESTORE_DATABASE_URL sslpassword is forbidden' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE \
    RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal/electronic_mail_restore?sslpassword=tls-secret' \
    EXPECTED_RESTORE_DATABASE=electronic_mail_restore BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
    BACKUP_FILE="$backup_file" RESTORE_TEST_LOG="$TEST_DIR/tls-key-password-in-url.log" bash "$SCRIPT"
[ ! -e "$TEST_DIR/tls-key-password-in-url.log" ] \
  || fail "pg_restore ran with a TLS private-key password in RESTORE_DATABASE_URL"
grep -Fxq 'PGCONNECT_TIMEOUT=10' "$psql_log" || fail "psql preflight did not bound connection time"
grep -Fxq 'PGOPTIONS=-c statement_timeout=30000 -c lock_timeout=10000 -c idle_session_timeout=0' "$psql_log" \
  || fail "persistent maintenance psql did not disable idle_session_timeout"
for required_psql_argument in --no-psqlrc --set=ON_ERROR_STOP=1 --tuples-only --no-align --quiet; do
  grep -Fxq "ARG=$required_psql_argument" "$psql_log" \
    || fail "psql preflight missed argument $required_psql_argument"
done
if grep -Fxq 'ARG=--single-transaction' "$psql_log"; then
  fail "target psql used EOF-committing --single-transaction instead of an explicit COMMIT"
fi
[ "$(maintenance_call_count)" -eq 1 ] \
  || fail "restore used more than one maintenance psql process"
if grep -Fxq 'ARG=--command' "$psql_log"; then
  fail "maintenance operations used fresh --command connections instead of one persistent session"
fi
success_events="$(tr '\n' '|' < "$event_log")"
[ "$success_events" = 'TARGET_SESSION_CONNECTED|TARGET_BACKEND_PID=4242|RESTORE_LOCK_ACQUIRED|TARGET_TRANSACTION_STARTED|MAINTENANCE_SESSION_CONNECTED|MAINTENANCE_IDENTIFIED_TARGET|CONNECTIONS_DISABLE_ATTEMPTED|CONNECTIONS_DISABLED|MAINTENANCE_RECONFIRMED_TARGET|TERMINATION_ATTEMPTED|SOLE_TARGET_BACKEND_CONFIRMED|PRISTINE_PREFLIGHT_PASSED|PG_RESTORE_GENERATED_SQL|RESTORE_SQL_IN_TARGET_SESSION|STATUS_NONCE_INDEPENDENT|TARGET_PRECOMMIT_ACKNOWLEDGED|TARGET_TRANSACTION_COMMITTED|TARGET_COMMIT_ACKNOWLEDGED|CONNECTIONS_REENABLE_ATTEMPTED|CONNECTIONS_ENABLED|MAINTENANCE_SESSION_CLOSED|' ] \
  || fail "restore isolation/transaction sequence was not exact: $success_events"

ipv4_restore_log="$TEST_DIR/ipv4-pg-restore.log"
: > "$event_log"
run_isolated_restore "$ipv4_restore_log" \
  RESTORE_DATABASE_URL='postgresql://restore_user:secret@127.0.0.1:5433/electronic_mail_restore?sslmode=require' \
  RESTORE_MAINTENANCE_DATABASE_URL='postgresql://maintenance_user:secret@127.0.0.1:5433/postgres?sslmode=require' \
  > "$TEST_DIR/ipv4-restore.stdout"
grep -Fxq 'INPUT_CONTENT=verified backup data' "$ipv4_restore_log" \
  || fail "canonical IPv4 restore endpoint did not complete"
grep -Fxq 'ARG=--dbname=postgresql://restore_user@127.0.0.1:5433/electronic_mail_restore?sslmode=require' \
  "$psql_log" || fail "canonical IPv4 restore endpoint was altered or rejected"

: > "$event_log"
expect_failure concurrent-restore 'Another restore is already running on this target database.' \
  run_isolated_restore "$TEST_DIR/concurrent-restore-pg-restore.log" RESTORE_TEST_LOCK_UNAVAILABLE=1
grep -Fxq 'RESTORE_LOCK_REJECTED' "$event_log" \
  || fail "concurrent restore did not fail at the target-database restore lock"
if grep -Fxq 'MAINTENANCE_SESSION_CONNECTED' "$event_log"; then
  fail "concurrent restore rejection started a maintenance session"
fi
if grep -Fxq 'CONNECTIONS_ENABLED' "$event_log"; then
  fail "concurrent restore rejection re-enabled connections owned by another restore"
fi

for maintenance_identity_state in WRONG_CLUSTER MISSING_TARGET; do
  identity_label="$(printf '%s' "$maintenance_identity_state" | tr '[:upper:]_' '[:lower:]-')"
  identity_restore_log="$TEST_DIR/$identity_label-pg-restore.log"
  maintenance_calls_before="$(maintenance_call_count)"
  : > "$event_log"
  expect_failure "$identity_label" \
    'Persistent maintenance session could not identify the nonce-bound target restore session' \
    run_isolated_restore "$identity_restore_log" \
      RESTORE_TEST_MAINTENANCE_IDENTITY_STATE="$maintenance_identity_state"
  [ ! -e "$identity_restore_log" ] \
    || fail "$identity_label reached pg_restore before binding the maintenance cluster"
  [ "$(maintenance_call_count)" -eq $((maintenance_calls_before + 1)) ] \
    || fail "$identity_label used more than one maintenance psql process"
  grep -Fxq "MAINTENANCE_IDENTITY_REJECTED_$maintenance_identity_state" "$event_log" \
    || fail "$identity_label did not reject the unbound maintenance session"
  grep -Fxq 'TARGET_TRANSACTION_ROLLED_BACK' "$event_log" \
    || fail "$identity_label did not roll back the target transaction"
  if grep -Eq '^(CONNECTIONS_DISABLE_ATTEMPTED|CONNECTIONS_DISABLED|TERMINATION_ATTEMPTED|CONNECTIONS_REENABLE_ATTEMPTED|CONNECTIONS_ENABLED)$' "$event_log"; then
    fail "$identity_label altered or terminated through an unbound maintenance session"
  fi
done

isolation_failure_log="$TEST_DIR/isolation-failure-pg-restore.log"
maintenance_calls_before="$(maintenance_call_count)"
: > "$event_log"
expect_failure isolation-failure \
  'Could not make the restore session the sole target backend' \
  run_isolated_restore "$isolation_failure_log" RESTORE_TEST_ISOLATION_FAIL=1
[ ! -e "$isolation_failure_log" ] || fail "isolation failure reached pg_restore"
[ "$(maintenance_call_count)" -eq $((maintenance_calls_before + 1)) ] \
  || fail "isolation failure did not retain one persistent maintenance process"
isolation_events="$(tr '\n' '|' < "$event_log")"
[ "$isolation_events" = 'TARGET_SESSION_CONNECTED|TARGET_BACKEND_PID=4242|RESTORE_LOCK_ACQUIRED|TARGET_TRANSACTION_STARTED|MAINTENANCE_SESSION_CONNECTED|MAINTENANCE_IDENTIFIED_TARGET|CONNECTIONS_DISABLE_ATTEMPTED|CONNECTIONS_DISABLED|MAINTENANCE_RECONFIRMED_TARGET|TERMINATION_ATTEMPTED|TARGET_ISOLATION_REJECTED|TARGET_TRANSACTION_ROLLED_BACK|CONNECTIONS_REENABLE_ATTEMPTED|CONNECTIONS_ENABLED|MAINTENANCE_SESSION_CLOSED|' ] \
  || fail "isolation failure did not roll back and re-enable through the same ordered session: $isolation_events"

maintenance_loss_log="$TEST_DIR/maintenance-loss-pg-restore.log"
maintenance_calls_before="$(maintenance_call_count)"
: > "$event_log"
expect_failure maintenance-loss-after-disable \
  'Could not isolate the restore target' \
  run_isolated_restore "$maintenance_loss_log" RESTORE_TEST_MAINTENANCE_LOSS_AFTER_DISABLE=1
[ ! -e "$maintenance_loss_log" ] || fail "lost maintenance session reached pg_restore"
[ "$(maintenance_call_count)" -eq $((maintenance_calls_before + 1)) ] \
  || fail "maintenance loss opened a replacement maintenance process"
grep -Fq 'CRITICAL: the persistent maintenance session could not confirm re-enabling connections' \
  "$TEST_DIR/maintenance-loss-after-disable.stderr" \
  || fail "maintenance loss after disable did not require manual recovery"
if grep -Eq '^(TERMINATION_ATTEMPTED|CONNECTIONS_REENABLE_ATTEMPTED|CONNECTIONS_ENABLED)$' "$event_log"; then
  fail "maintenance loss blindly terminated or re-enabled through another connection"
fi
if grep -Eq 'ARG=.*(--dbname|s3cr|postgresql://|electronic-mail-test\.dump\.enc)' "$success_log"; then
  fail "pg_restore arguments exposed connection details or the encrypted artifact instead of plaintext"
fi
pgpass_path="$(awk -F= '$1 == "PGPASSFILE" && length($0) > length($1) + 1 { print substr($0, index($0, "=") + 1); exit }' "$psql_log")"
[ -n "$pgpass_path" ] && [ ! -e "$pgpass_path" ] || fail "temporary PGPASSFILE was not removed"
if grep -Eq '^ARG=[^-]' "$success_log"; then
  fail "pg_restore received a plaintext archive path instead of stdin"
fi
if find "$TEST_DIR" -type f -name 'restore.dump' -print -quit | grep -q .; then
  fail "restore wrote plaintext to a temporary dump"
fi

nonempty_log="$TEST_DIR/nonempty-pg-restore.log"
: > "$event_log"
expect_failure nonempty-target 'Refusing restore: target database is not empty' \
  run_isolated_restore "$nonempty_log" RESTORE_TEST_TARGET_STATE=NONEMPTY
[ ! -e "$nonempty_log" ] || fail "pg_restore ran against a nonempty target"
grep -Fxq 'CONNECTIONS_ENABLED' "$event_log" \
  || fail "nonempty-target rejection did not re-enable connections"

hostile_metadata_log="$TEST_DIR/hostile-metadata-pg-restore.log"
: > "$event_log"
expect_failure hostile-metadata 'public schema metadata is not pristine' \
  run_isolated_restore "$hostile_metadata_log" RESTORE_TEST_TARGET_STATE=HOSTILE_METADATA
[ ! -e "$hostile_metadata_log" ] || fail "pg_restore ran with hostile public-schema metadata"
grep -Fxq 'CONNECTIONS_ENABLED' "$event_log" \
  || fail "hostile-metadata rejection did not re-enable connections"

preflight_failure_log="$TEST_DIR/preflight-failure-pg-restore.log"
: > "$event_log"
expect_failure preflight-failure 'Could not prove the isolated restore target is pristine; restore was not started.' \
  run_isolated_restore "$preflight_failure_log" RESTORE_TEST_PREFLIGHT_FAIL=1
[ ! -e "$preflight_failure_log" ] || fail "pg_restore ran after the empty-target preflight failed"
grep -Fxq 'CONNECTIONS_ENABLED' "$event_log" \
  || fail "preflight execution failure did not re-enable connections"

unexpected_preflight_log="$TEST_DIR/unexpected-preflight-pg-restore.log"
: > "$event_log"
expect_failure unexpected-preflight 'Could not prove the isolated restore target is pristine; restore was not started.' \
  run_isolated_restore "$unexpected_preflight_log" RESTORE_TEST_TARGET_STATE=UNKNOWN
[ ! -e "$unexpected_preflight_log" ] || fail "pg_restore ran after an ambiguous empty-target preflight"
grep -Fxq 'CONNECTIONS_ENABLED' "$event_log" \
  || fail "ambiguous preflight failure did not re-enable connections"

empty_child_secrets='DATABASE_URL=|RESTORE_DATABASE_URL=|RESTORE_MAINTENANCE_DATABASE_URL=|BACKUP_ENCRYPTION_KEY=|PGDATABASE=|PGPASSWORD=|PGPASSFILE=|raw_database_url=|raw_restore_database_url=|raw_restore_maintenance_database_url=|backup_encryption_key='
for early_child in dirname mktemp; do
  grep -Fxq "CHILD=$early_child|$empty_child_secrets" "$secret_lifetime_log" \
    || fail "$early_child inherited a captured restore secret"
done
grep -Fxq "CHILD=validate-key|DATABASE_URL=|RESTORE_DATABASE_URL=|RESTORE_MAINTENANCE_DATABASE_URL=|BACKUP_ENCRYPTION_KEY=$BACKUP_KEY|PGDATABASE=|PGPASSWORD=|PGPASSFILE=|raw_database_url=|raw_restore_database_url=|raw_restore_maintenance_database_url=|backup_encryption_key=" \
  "$secret_lifetime_log" || fail "key validation inherited the restore URL or missed its scoped key"
grep -Fxq "CHILD=sanitize|DATABASE_URL=|RESTORE_DATABASE_URL=$success_restore_database_url|RESTORE_MAINTENANCE_DATABASE_URL=$success_maintenance_database_url|BACKUP_ENCRYPTION_KEY=|PGDATABASE=|PGPASSWORD=|PGPASSFILE=|raw_database_url=|raw_restore_database_url=|raw_restore_maintenance_database_url=|backup_encryption_key=" \
  "$secret_lifetime_log" || fail "URL sanitizer inherited the encryption key or missed its scoped URL"
grep -Fxq "CHILD=decrypt-stdout|DATABASE_URL=|RESTORE_DATABASE_URL=|RESTORE_MAINTENANCE_DATABASE_URL=|BACKUP_ENCRYPTION_KEY=$BACKUP_KEY|PGDATABASE=|PGPASSWORD=|PGPASSFILE=|raw_database_url=|raw_restore_database_url=|raw_restore_maintenance_database_url=|backup_encryption_key=" \
  "$secret_lifetime_log" || fail "decryption inherited the restore URL or missed its scoped key"
if grep -Eq '\|(raw_database_url|raw_restore_database_url|raw_restore_maintenance_database_url|backup_encryption_key)=[^|]' "$secret_lifetime_log"; then
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
maintenance_calls_before="$(maintenance_call_count)"
: > "$event_log"
set +e
run_isolated_restore "$failure_log" RESTORE_TEST_FAIL=1 > "$TEST_DIR/failure.stdout" 2> "$TEST_DIR/failure.stderr"
failure_status=$?
set -e
[ "$failure_status" -eq 43 ] || fail "pg_restore failure status was not preserved"
[ "$(maintenance_call_count)" -eq $((maintenance_calls_before + 1)) ] \
  || fail "pg_restore generation failure replaced the persistent maintenance process"
failure_pgpass_path="$(awk -F= '$1 == "PGPASSFILE" && $0 ~ /target[.]pgpass$/ { value=substr($0, index($0, "=") + 1) } END { print value }' "$psql_log")"
[ -n "$failure_pgpass_path" ] && [ ! -e "$failure_pgpass_path" ] \
  || fail "temporary credentials remained after pg_restore failed"
grep -Fxq 'CONNECTIONS_ENABLED' "$event_log" \
  || fail "pg_restore generation failure did not re-enable target connections"
grep -Fxq 'TARGET_TRANSACTION_ROLLED_BACK' "$event_log" \
  || fail "pg_restore generation failure did not roll back the open target transaction"
if grep -Fxq 'TARGET_TRANSACTION_COMMITTED' "$event_log"; then
  fail "pg_restore generation failure sent COMMIT to the target session"
fi
generation_failure_events="$(tr '\n' '|' < "$event_log")"
case "$generation_failure_events" in
  *'TARGET_TRANSACTION_ROLLED_BACK|CONNECTIONS_REENABLE_ATTEMPTED|CONNECTIONS_ENABLED|MAINTENANCE_SESSION_CLOSED|'*) ;;
  *) fail "generation failure did not roll back before same-session re-enable: $generation_failure_events" ;;
esac
if find "$TEST_DIR" -type f -name 'restore.dump' -print -quit | grep -q .; then
  fail "pg_restore failure left a plaintext dump behind"
fi

target_failure_log="$TEST_DIR/target-failure-pg-restore.log"
maintenance_calls_before="$(maintenance_call_count)"
: > "$event_log"
expect_failure target-session-failure \
  'Atomic restore transaction failed before COMMIT' \
  run_isolated_restore "$target_failure_log" RESTORE_TEST_PSQL_FAIL=1
[ "$(maintenance_call_count)" -eq $((maintenance_calls_before + 1)) ] \
  || fail "target failure replaced the persistent maintenance process"
grep -Fxq 'TARGET_TRANSACTION_ROLLED_BACK' "$event_log" \
  || fail "target failure did not roll back before COMMIT"
grep -Fxq 'CONNECTIONS_ENABLED' "$event_log" \
  || fail "target failure did not re-enable through the persistent maintenance session"
if grep -Fxq 'TARGET_TRANSACTION_COMMITTED' "$event_log"; then
  fail "target failure attempted COMMIT after the precommit acknowledgment failed"
fi

missing_commit_ack_log="$TEST_DIR/missing-commit-ack-pg-restore.log"
maintenance_calls_before="$(maintenance_call_count)"
: > "$event_log"
expect_failure missing-commit-ack \
  'CRITICAL: the restore COMMIT outcome is indeterminate' \
  run_isolated_restore "$missing_commit_ack_log" RESTORE_TEST_SUPPRESS_COMMIT_ACK=1
[ "$(maintenance_call_count)" -eq $((maintenance_calls_before + 1)) ] \
  || fail "missing commit acknowledgment opened another maintenance process"
grep -Fxq 'TARGET_TRANSACTION_COMMITTED' "$event_log" \
  || fail "missing-ack test did not reach the indeterminate COMMIT boundary"
grep -Fxq 'TARGET_COMMIT_ACK_SUPPRESSED' "$event_log" \
  || fail "missing-ack test did not suppress the commit sentinel"
if grep -Eq '^(TARGET_TRANSACTION_ROLLED_BACK|CONNECTIONS_REENABLE_ATTEMPTED|CONNECTIONS_ENABLED)$' "$event_log"; then
  fail "indeterminate COMMIT was mislabeled as rollback or automatically re-enabled"
fi

acknowledged_odd_exit_log="$TEST_DIR/acknowledged-odd-exit-pg-restore.log"
maintenance_calls_before="$(maintenance_call_count)"
: > "$event_log"
run_isolated_restore "$acknowledged_odd_exit_log" \
  RESTORE_TEST_TARGET_EXIT_AFTER_ACK=44 > "$TEST_DIR/acknowledged-odd-exit.stdout"
[ "$(maintenance_call_count)" -eq $((maintenance_calls_before + 1)) ] \
  || fail "acknowledged target exit opened another maintenance process"
grep -Fxq 'TARGET_COMMIT_ACKNOWLEDGED' "$event_log" \
  || fail "odd target exit did not emit the authoritative commit acknowledgment"
grep -Fxq 'CONNECTIONS_ENABLED' "$event_log" \
  || fail "acknowledged COMMIT was not re-enabled after the target client exited oddly"

reenable_failure_log="$TEST_DIR/reenable-failure-pg-restore.log"
maintenance_calls_before="$(maintenance_call_count)"
: > "$event_log"
expect_failure reenable-failure \
  'CRITICAL: the persistent maintenance session could not confirm re-enabling connections' \
  run_isolated_restore "$reenable_failure_log" RESTORE_TEST_REENABLE_FAIL=1
[ "$(maintenance_call_count)" -eq $((maintenance_calls_before + 1)) ] \
  || fail "re-enable failure opened a replacement maintenance process"
[ "$(grep -c '^CONNECTIONS_REENABLE_ATTEMPTED$' "$event_log")" -eq 1 ] \
  || fail "re-enable failure retried instead of failing critically"
if grep -Fxq 'CONNECTIONS_ENABLED' "$event_log"; then
  fail "failed re-enable was reported as confirmed"
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
    RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal/electronic_mail?sslmode=verify-full&sslrootcert=system&gssencmode=disable' \
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

expect_failure production-missing-host 'EXPECTED_RESTORE_HOST is required to bind a production restore' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE CONFIRM_PRODUCTION_RESTORE=RESTORE_PRODUCTION_DATABASE \
    RESTORE_TARGET_CLASS=production \
    RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal/electronic_mail?sslmode=verify-full&sslrootcert=system&gssencmode=disable' \
    EXPECTED_RESTORE_DATABASE=electronic_mail BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
    BACKUP_FILE="$backup_file" RESTORE_TEST_LOG="$TEST_DIR/production-missing-host.log" bash "$SCRIPT"
[ ! -e "$TEST_DIR/production-missing-host.log" ] || fail "pg_restore ran without a host-bound production confirmation"

expect_failure production-missing-port 'EXPECTED_RESTORE_PORT is required to bind a production restore' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE CONFIRM_PRODUCTION_RESTORE=RESTORE_PRODUCTION_DATABASE \
    RESTORE_TARGET_CLASS=production \
    RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal/electronic_mail' \
    EXPECTED_RESTORE_DATABASE=electronic_mail EXPECTED_RESTORE_HOST=db.internal \
    BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" BACKUP_FILE="$backup_file" \
    RESTORE_TEST_LOG="$TEST_DIR/production-missing-port.log" bash "$SCRIPT"
[ ! -e "$TEST_DIR/production-missing-port.log" ] || fail "pg_restore ran without a port-bound production confirmation"

expect_failure production-wrong-host 'Restore host mismatch: expected other-db.internal, URL targets db.internal' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE CONFIRM_PRODUCTION_RESTORE=RESTORE_PRODUCTION_DATABASE \
    RESTORE_TARGET_CLASS=production \
    RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal/electronic_mail?sslmode=verify-full&sslrootcert=system&gssencmode=disable' \
    EXPECTED_RESTORE_DATABASE=electronic_mail EXPECTED_RESTORE_HOST=other-db.internal EXPECTED_RESTORE_PORT=5432 \
    BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" BACKUP_FILE="$backup_file" \
    RESTORE_TEST_LOG="$TEST_DIR/production-wrong-host.log" bash "$SCRIPT"
[ ! -e "$TEST_DIR/production-wrong-host.log" ] || fail "pg_restore ran for a mismatched production host"

expect_failure production-wrong-port 'Restore port mismatch: expected 5433, URL targets 5432' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE CONFIRM_PRODUCTION_RESTORE=RESTORE_PRODUCTION_DATABASE \
    RESTORE_TARGET_CLASS=production \
    RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal/electronic_mail?sslmode=verify-full&sslrootcert=system&gssencmode=disable' \
    EXPECTED_RESTORE_DATABASE=electronic_mail EXPECTED_RESTORE_HOST=db.internal EXPECTED_RESTORE_PORT=5433 \
    BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" BACKUP_FILE="$backup_file" \
    RESTORE_TEST_LOG="$TEST_DIR/production-wrong-port.log" bash "$SCRIPT"
[ ! -e "$TEST_DIR/production-wrong-port.log" ] || fail "pg_restore ran for a mismatched production port"

expect_failure production-missing-verified-tls 'production restore requires sslmode=verify-full' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE CONFIRM_PRODUCTION_RESTORE=RESTORE_PRODUCTION_DATABASE \
    RESTORE_TARGET_CLASS=production \
    RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal:5433/electronic_mail?sslmode=require&sslrootcert=system' \
    EXPECTED_RESTORE_DATABASE=electronic_mail EXPECTED_RESTORE_HOST=db.internal EXPECTED_RESTORE_PORT=5433 \
    BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" BACKUP_FILE="$backup_file" \
    RESTORE_TEST_LOG="$TEST_DIR/production-missing-verified-tls.log" bash "$SCRIPT"
[ ! -e "$TEST_DIR/production-missing-verified-tls.log" ] || fail "pg_restore ran without authenticated production TLS"

expect_failure production-missing-root-cert 'production restore requires an explicit sslrootcert trust source' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE CONFIRM_PRODUCTION_RESTORE=RESTORE_PRODUCTION_DATABASE \
    RESTORE_TARGET_CLASS=production \
    RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal:5433/electronic_mail?sslmode=verify-full&gssencmode=disable' \
    EXPECTED_RESTORE_DATABASE=electronic_mail EXPECTED_RESTORE_HOST=db.internal EXPECTED_RESTORE_PORT=5433 \
    BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" BACKUP_FILE="$backup_file" \
    RESTORE_TEST_LOG="$TEST_DIR/production-missing-root-cert.log" bash "$SCRIPT"
[ ! -e "$TEST_DIR/production-missing-root-cert.log" ] || fail "pg_restore ran without an explicit production trust source"

expect_failure production-gss-bypass 'production restore requires gssencmode=disable' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE CONFIRM_PRODUCTION_RESTORE=RESTORE_PRODUCTION_DATABASE \
    RESTORE_TARGET_CLASS=production \
    RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal:5433/electronic_mail?sslmode=verify-full&sslrootcert=system&gssencmode=require' \
    EXPECTED_RESTORE_DATABASE=electronic_mail EXPECTED_RESTORE_HOST=db.internal EXPECTED_RESTORE_PORT=5433 \
    BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" BACKUP_FILE="$backup_file" \
    RESTORE_TEST_LOG="$TEST_DIR/production-gss-bypass.log" bash "$SCRIPT"
[ ! -e "$TEST_DIR/production-gss-bypass.log" ] || fail "pg_restore ran with GSS able to bypass TLS verification"

expect_failure production-old-client 'Restores require the reviewed PostgreSQL 17.10 or newer 17.x pg_restore client' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE CONFIRM_PRODUCTION_RESTORE=RESTORE_PRODUCTION_DATABASE \
    RESTORE_TARGET_CLASS=production RESTORE_TEST_PG_VERSION=15.9 \
    RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal:5433/electronic_mail?sslmode=verify-full&sslrootcert=system&gssencmode=disable' \
    EXPECTED_RESTORE_DATABASE=electronic_mail EXPECTED_RESTORE_HOST=db.internal EXPECTED_RESTORE_PORT=5433 \
    BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" BACKUP_FILE="$backup_file" \
    RESTORE_TEST_LOG="$TEST_DIR/production-old-client.log" bash "$SCRIPT"
[ ! -e "$TEST_DIR/production-old-client.log" ] || fail "pg_restore ran with an unreviewed production client major"

expect_failure production-old-psql 'Restores require the reviewed PostgreSQL 17.10 or newer 17.x psql client' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE CONFIRM_PRODUCTION_RESTORE=RESTORE_PRODUCTION_DATABASE \
    RESTORE_TARGET_CLASS=production RESTORE_TEST_PSQL_VERSION=16.4 \
    RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal:5433/electronic_mail?sslmode=verify-full&sslrootcert=system&gssencmode=disable' \
    EXPECTED_RESTORE_DATABASE=electronic_mail EXPECTED_RESTORE_HOST=db.internal EXPECTED_RESTORE_PORT=5433 \
    BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" BACKUP_FILE="$backup_file" \
    RESTORE_TEST_LOG="$TEST_DIR/production-old-psql.log" bash "$SCRIPT"
[ ! -e "$TEST_DIR/production-old-psql.log" ] || fail "pg_restore ran with an unreviewed production psql client major"

expect_failure vulnerable-pg-restore-patch 'Restores require the reviewed PostgreSQL 17.10 or newer 17.x pg_restore client' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE RESTORE_TEST_PG_VERSION=17.9 \
    RESTORE_DATABASE_URL="$success_restore_database_url" \
    RESTORE_MAINTENANCE_DATABASE_URL="$success_maintenance_database_url" \
    EXPECTED_RESTORE_DATABASE=electronic_mail_restore BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
    BACKUP_FILE="$backup_file" RESTORE_TEST_LOG="$TEST_DIR/vulnerable-pg-restore-patch.log" bash "$SCRIPT"
[ ! -e "$TEST_DIR/vulnerable-pg-restore-patch.log" ] \
  || fail "pg_restore ran with an unreviewed vulnerable patch level"

expect_failure vulnerable-psql-patch 'Restores require the reviewed PostgreSQL 17.10 or newer 17.x psql client' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE RESTORE_TEST_PSQL_VERSION=17.9 \
    RESTORE_DATABASE_URL="$success_restore_database_url" \
    RESTORE_MAINTENANCE_DATABASE_URL="$success_maintenance_database_url" \
    EXPECTED_RESTORE_DATABASE=electronic_mail_restore BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" \
    BACKUP_FILE="$backup_file" RESTORE_TEST_LOG="$TEST_DIR/vulnerable-psql-patch.log" bash "$SCRIPT"
[ ! -e "$TEST_DIR/vulnerable-psql-patch.log" ] \
  || fail "psql ran with an unreviewed vulnerable patch level"

expect_failure production-zero-port 'RESTORE_DATABASE_URL port must be between 1 and 65535' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE CONFIRM_PRODUCTION_RESTORE=RESTORE_PRODUCTION_DATABASE \
    RESTORE_TARGET_CLASS=production \
    RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal:0/electronic_mail?sslmode=verify-full&sslrootcert=system&gssencmode=disable' \
    EXPECTED_RESTORE_DATABASE=electronic_mail EXPECTED_RESTORE_HOST=db.internal EXPECTED_RESTORE_PORT=5432 \
    BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" BACKUP_FILE="$backup_file" \
    RESTORE_TEST_LOG="$TEST_DIR/production-zero-port.log" bash "$SCRIPT"
[ ! -e "$TEST_DIR/production-zero-port.log" ] || fail "pg_restore ran for an explicit zero port"

expect_failure production-noncanonical-host 'EXPECTED_RESTORE_HOST must use canonical lowercase ASCII DNS labels' \
  env PATH="$test_path" CONFIRM_RESTORE=RESTORE CONFIRM_PRODUCTION_RESTORE=RESTORE_PRODUCTION_DATABASE \
    RESTORE_TARGET_CLASS=production \
    RESTORE_DATABASE_URL='postgresql://restore_user:secret@db_internal:5433/electronic_mail?sslmode=verify-full&sslrootcert=system&gssencmode=disable' \
    EXPECTED_RESTORE_DATABASE=electronic_mail EXPECTED_RESTORE_HOST=db_internal EXPECTED_RESTORE_PORT=5433 \
    BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" BACKUP_FILE="$backup_file" \
    RESTORE_TEST_LOG="$TEST_DIR/production-noncanonical-host.log" bash "$SCRIPT"
[ ! -e "$TEST_DIR/production-noncanonical-host.log" ] || fail "pg_restore ran for a noncanonical production hostname"

production_log="$TEST_DIR/production-bound-target.log"
env PATH="$test_path" CONFIRM_RESTORE=RESTORE CONFIRM_PRODUCTION_RESTORE=RESTORE_PRODUCTION_DATABASE \
  RESTORE_TARGET_CLASS=production \
  RESTORE_DATABASE_URL='postgresql://restore_user:secret@db.internal:5433/electronic_mail?sslmode=verify-full&sslrootcert=system&gssencmode=disable' \
  RESTORE_MAINTENANCE_DATABASE_URL="$success_maintenance_database_url" \
  EXPECTED_RESTORE_DATABASE=electronic_mail EXPECTED_RESTORE_HOST=db.internal EXPECTED_RESTORE_PORT=5433 \
  BACKUP_ENCRYPTION_KEY="$BACKUP_KEY" BACKUP_FILE="$backup_file" \
  RESTORE_TEST_LOG="$production_log" bash "$SCRIPT" > "$TEST_DIR/production-bound-target.stdout"
grep -Fxq 'INPUT_CONTENT=verified backup data' "$production_log" \
  || fail "host-and-port-bound production restore did not complete"
grep -Fxq 'ARG=--dbname=postgresql://restore_user@db.internal:5433/electronic_mail?sslmode=verify-full&sslrootcert=system&gssencmode=disable' "$psql_log" \
  || fail "production restore did not retain its verified TLS policy"

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

echo "restore-postgres authenticated-encryption, credential, exact-target, and checksum tests passed"
