from __future__ import annotations

"""Reset Postgres app data while preserving the schema."""

from app.core.config import load_settings
from app.db.repository import get_engine

PRESERVED_TABLES = {
    "alembic_version",
    "allowed_emails",
}


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _runtime_tables(connection) -> list[str]:
    rows = connection.exec_driver_sql(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """
    ).fetchall()
    return [str(row[0]) for row in rows if str(row[0]) not in PRESERVED_TABLES]


def main() -> None:
    settings = load_settings()
    if settings.database_backend != "postgres":
        raise SystemExit("DATABASE_URL must be Postgres")
    with get_engine(str(settings.database_path)).begin() as connection:
        tables = _runtime_tables(connection)
        if tables:
            joined_tables = ", ".join(_quote_identifier(table) for table in tables)
            connection.exec_driver_sql(f"TRUNCATE TABLE {joined_tables} RESTART IDENTITY CASCADE")
    print(f"Reset Postgres app data ({len(tables)} tables).")


if __name__ == "__main__":
    main()
