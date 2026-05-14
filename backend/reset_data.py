from __future__ import annotations

"""Reset Postgres app data while preserving the schema."""

from app.core.config import load_settings
from app.db.repository import get_engine

TABLES = [
    "background_job_events",
    "background_jobs",
    "worker_heartbeats",
    "mail_group_members",
    "mail_groups",
    "gmail_messages",
    "gmail_import_state",
    "google_oauth_tokens",
    "mobile_login_codes",
    "oauth_login_sessions",
    "app_sessions",
    "users",
]


def main() -> None:
    settings = load_settings()
    if settings.database_backend != "postgres":
        raise SystemExit("DATABASE_URL must be Postgres")
    with get_engine(str(settings.database_path)).begin() as connection:
        for table in TABLES:
            connection.exec_driver_sql(f"DELETE FROM {table}")
    print("Reset Postgres app data.")


if __name__ == "__main__":
    main()
