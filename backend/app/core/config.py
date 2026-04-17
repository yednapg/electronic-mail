from __future__ import annotations

"""Runtime configuration loading for the Python backend."""

from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
import os


BACKEND_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BACKEND_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    """Resolved environment settings used across the backend."""

    port: int
    database_url: str
    database_path: Path
    cors_origin: str
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def backend_origin(self) -> str:
        redirect_suffix = "/auth/google/callback"

        if self.google_redirect_uri.endswith(redirect_suffix):
            return self.google_redirect_uri[: -len(redirect_suffix)]

        return f"http://localhost:{self.port}"


def _resolve_database_path(database_url: str) -> Path:
    """Normalize either a file: URL or plain path into an absolute SQLite path."""
    normalized = database_url.strip().strip("\"'")

    if normalized.startswith("file:"):
        return (BACKEND_DIR / normalized.removeprefix("file:")).resolve()

    return Path(normalized).expanduser().resolve()


def load_settings() -> Settings:
    """Load environment variables once and expose a typed settings object."""
    database_url = os.getenv("DATABASE_URL", "file:./dev.db")

    return Settings(
        port=int(os.getenv("PORT", "3001")),
        database_url=database_url,
        database_path=_resolve_database_path(database_url),
        cors_origin=os.getenv("CORS_ORIGIN", "http://localhost:5173"),
        google_client_id=os.getenv("GOOGLE_CLIENT_ID", "").strip().strip("\"'"),
        google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", "").strip().strip("\"'"),
        google_redirect_uri=os.getenv(
            "GOOGLE_REDIRECT_URI",
            "http://localhost:3001/auth/google/callback",
        ),
    )
