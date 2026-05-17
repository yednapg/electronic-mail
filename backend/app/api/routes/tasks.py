from __future__ import annotations

"""Backend-owned manual task endpoints."""

from fastapi import APIRouter, HTTPException, Request, status

from app.core.config import load_settings
from app.db.mail_groups import create_manual_task, get_manual_task, update_manual_task
from app.schemas.domain import TaskCreateRequest, TaskResponse, TaskUpdateRequest
from app.services.auth import require_current_user
from app.services.mail_groups import refresh_app_session_snapshot


router = APIRouter(tags=["tasks"])
settings = load_settings()


@router.post("/v1/tasks", response_model=TaskResponse)
def create_task(http_request: Request, request: TaskCreateRequest) -> TaskResponse:
    user = require_current_user(settings, http_request)
    title = request.title.strip()
    if not title:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Task title is required")
    notes = request.notes.strip() if isinstance(request.notes, str) and request.notes.strip() else None
    task = create_manual_task(
        str(settings.database_path),
        user_id=user.id,
        title=title,
        notes=notes,
        section=request.section,
        due_at=request.due_at,
    )
    refresh_app_session_snapshot(settings, user_id=user.id)
    return _task_response(task)


@router.get("/v1/tasks/{task_id}", response_model=TaskResponse)
def read_task(http_request: Request, task_id: str) -> TaskResponse:
    user = require_current_user(settings, http_request)
    task = get_manual_task(str(settings.database_path), task_id, user_id=user.id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return _task_response(task)


@router.patch("/v1/tasks/{task_id}", response_model=TaskResponse)
def patch_task(http_request: Request, task_id: str, request: TaskUpdateRequest) -> TaskResponse:
    user = require_current_user(settings, http_request)
    title = request.title.strip() if isinstance(request.title, str) and request.title.strip() else None
    notes = request.notes.strip() if isinstance(request.notes, str) and request.notes.strip() else None
    task = update_manual_task(
        str(settings.database_path),
        task_id,
        user_id=user.id,
        title=title,
        notes=notes,
        section=request.section,
        due_at=request.due_at,
        status=request.status,
    )
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    refresh_app_session_snapshot(settings, user_id=user.id)
    return _task_response(task)


def _task_response(task) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        user_id=task.user_id,
        entity_id=task.entity_id,
        title=task.title,
        notes=task.notes,
        section=task.section,
        due_at=task.due_at,
        status=task.status,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )
