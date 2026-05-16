#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_NAME="${LOCAL_POSTGRES_DB:-electronic_mail}"
DATABASE_URL_VALUE="postgresql:///$DB_NAME"
ENV_LOCAL_FILE="$ROOT_DIR/backend/.env.local"
BREW_FORMULA="${LOCAL_POSTGRES_BREW_FORMULA:-postgresql@16}"

find_pg_cmd() {
  local name="$1"
  if command -v "$name" >/dev/null 2>&1; then
    command -v "$name"
    return 0
  fi

  local roots=(
    "/opt/homebrew/opt/postgresql@17/bin"
    "/opt/homebrew/opt/postgresql@16/bin"
    "/opt/homebrew/opt/postgresql@15/bin"
    "/opt/homebrew/opt/postgresql/bin"
    "/usr/local/opt/postgresql@17/bin"
    "/usr/local/opt/postgresql@16/bin"
    "/usr/local/opt/postgresql@15/bin"
    "/usr/local/opt/postgresql/bin"
  )

  for root in "${roots[@]}"; do
    if [ -x "$root/$name" ]; then
      printf "%s\n" "$root/$name"
      return 0
    fi
  done

  return 1
}

installed_brew_formula() {
  local candidates=("$BREW_FORMULA" "postgresql@17" "postgresql@16" "postgresql@15" "postgresql")
  for formula in "${candidates[@]}"; do
    if brew list --versions "$formula" >/dev/null 2>&1; then
      printf "%s\n" "$formula"
      return 0
    fi
  done
  return 1
}

set_env_value() {
  local file="$1"
  local key="$2"
  local value="$3"
  local tmp
  mkdir -p "$(dirname "$file")"
  touch "$file"
  tmp="$(mktemp)"
  awk -v key="$key" -v value="$value" '
    BEGIN { done = 0 }
    $0 ~ "^" key "=" {
      print key "=" value
      done = 1
      next
    }
    { print }
    END {
      if (!done) {
        print key "=" value
      }
    }
  ' "$file" > "$tmp"
  mv "$tmp" "$file"
}

echo "==> Setting up local Postgres database: $DB_NAME"

PSQL_BIN="$(find_pg_cmd psql || true)"
CREATEDB_BIN="$(find_pg_cmd createdb || true)"

if [ -z "$PSQL_BIN" ] || [ -z "$CREATEDB_BIN" ]; then
  if ! command -v brew >/dev/null 2>&1; then
    echo "Postgres client tools were not found, and Homebrew is not installed." >&2
    echo "Install Postgres, then re-run: npm run db:local:setup" >&2
    exit 1
  fi

  echo "==> Installing $BREW_FORMULA with Homebrew"
  brew install "$BREW_FORMULA"
  PSQL_BIN="$(find_pg_cmd psql)"
  CREATEDB_BIN="$(find_pg_cmd createdb)"
fi

if command -v brew >/dev/null 2>&1; then
  FORMULA="$(installed_brew_formula || true)"
  if [ -n "${FORMULA:-}" ]; then
    echo "==> Starting Homebrew service: $FORMULA"
    brew services start "$FORMULA" >/dev/null || true
  fi
fi

PG_ISREADY_BIN="$(find_pg_cmd pg_isready || true)"
if [ -n "$PG_ISREADY_BIN" ]; then
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if "$PG_ISREADY_BIN" -q >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

if "$PSQL_BIN" -d "$DB_NAME" -Atqc "SELECT 1" >/dev/null 2>&1; then
  echo "==> Database already exists: $DB_NAME"
else
  echo "==> Creating database: $DB_NAME"
  "$CREATEDB_BIN" "$DB_NAME"
fi

set_env_value "$ENV_LOCAL_FILE" "DATABASE_URL" "$DATABASE_URL_VALUE"
echo "==> Wrote local override: backend/.env.local"

if [ ! -x "$ROOT_DIR/.venv/bin/alembic" ]; then
  echo "Python dependencies are not installed. Run npm run setup, then re-run npm run db:local:setup." >&2
  exit 1
fi

echo "==> Applying Alembic migrations"
(
  cd "$ROOT_DIR/backend"
  DATABASE_URL="$DATABASE_URL_VALUE" ../.venv/bin/alembic upgrade head
)

echo "==> Local Postgres is ready at $DATABASE_URL_VALUE"
