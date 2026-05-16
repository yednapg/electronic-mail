from __future__ import annotations

"""Validate the configured backend Postgres database for local development."""

from app.core.config import load_settings
from app.db.repository import initialize_database


def main() -> None:
    """Validate the configured database URL. Schema migrations are managed by Alembic."""
    settings = load_settings()
    initialize_database(str(settings.database_path))
    print(f"Initialized backend database at {settings.database_path}")


if __name__ == "__main__":
    main()
