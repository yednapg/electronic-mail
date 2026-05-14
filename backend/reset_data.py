from __future__ import annotations

"""Clear persisted backend data without removing the schema."""

from app.core.config import load_settings
from app.db.repository import clear_all_data, initialize_database


def main() -> None:
    """Reset all persisted users, sessions, records, entities, and cached AI judgments."""
    settings = load_settings()
    initialize_database(str(settings.database_path))
    clear_all_data(str(settings.database_path))
    print("Reset backend data and app login state")


if __name__ == "__main__":
    main()
