from __future__ import annotations

"""FastAPI application entrypoint for the unified backend."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.auth_google import router as auth_google_router
from app.api.routes.app_session import router as app_session_router
from app.api.routes.gmail import router as gmail_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.first_run import router as first_run_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.mail_groups import router as mail_groups_router
from app.api.routes.post_login import router as post_login_router
from app.api.routes.system import router as system_router
from app.api.routes.mailbox import router as mailbox_router
from app.core.config import load_settings


settings = load_settings()

app = FastAPI(title="Mail Groups Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(system_router)
app.include_router(auth_google_router)
app.include_router(app_session_router)
app.include_router(first_run_router)
app.include_router(post_login_router)
app.include_router(dashboard_router)
app.include_router(gmail_router)
app.include_router(mailbox_router)
app.include_router(mail_groups_router)
app.include_router(jobs_router)
