from __future__ import annotations

"""Create the backend SQLite schema for local development."""

from app.core.config import load_settings
from app.db.repository import initialize_database


def main() -> None:
    """Initialize the configured local database path."""
    settings = load_settings()
    initialize_database(str(settings.database_path))
    print(f"Initialized backend database at {settings.database_path}")


if __name__ == "__main__":
    main()
