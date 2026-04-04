from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from dotenv import load_dotenv

from decision import decide_entities, describe_calendar_context
from schema import (
    CalendarContextRequest,
    CalendarContextResponse,
    DecideRequest,
    DecideResponse,
)

load_dotenv(Path(__file__).resolve().with_name(".env"))

app = FastAPI(title="AI Service")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "AI Service",
        "status": "ok",
        "health": "/health",
        "decide": "/decide",
        "docs": "/docs",
    }


@app.post("/decide", response_model=DecideResponse)
def decide(request: DecideRequest) -> DecideResponse:
    return DecideResponse(items=decide_entities(request.entities))


@app.post("/calendar-context", response_model=CalendarContextResponse)
def calendar_context(request: CalendarContextRequest) -> CalendarContextResponse:
    return CalendarContextResponse(items=describe_calendar_context(request.items))
