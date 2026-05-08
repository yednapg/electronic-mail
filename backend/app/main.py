from __future__ import annotations

"""FastAPI application entrypoint for the unified backend."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.ai import router as ai_router
from app.api.routes.auth_google import router as auth_google_router
from app.api.routes.gmail import router as gmail_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.entities import router as entities_router
from app.api.routes.feed import router as feed_router
from app.api.routes.system import router as system_router
from app.api.routes.history import router as history_router
from app.api.routes.tasks import router as tasks_router
from app.api.routes.trace import router as trace_router
from app.core.config import load_settings
from app.db.repository import initialize_database


settings = load_settings()
# Ensure the local SQLite schema exists before the app starts handling requests.
initialize_database(str(settings.database_path))

app = FastAPI(title="Decision Pipeline Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(system_router)
app.include_router(auth_google_router)
app.include_router(dashboard_router)
app.include_router(feed_router)
app.include_router(gmail_router)
app.include_router(tasks_router)
app.include_router(entities_router)
app.include_router(history_router)
app.include_router(trace_router)
app.include_router(ai_router)
