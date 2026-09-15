"""Durable task start, pause, and recovery operations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from neuro_os.models import Task, TaskStatus


class TaskContextError(RuntimeError):
    """Base error for task-context operations."""


class TaskNotFoundError(TaskContextError):
    """Raised when a task does not exist or belongs to another user."""


class InvalidTaskTransitionError(TaskContextError):
    """Raised when a task cannot enter the requested state."""


class MissingRecoveryContextError(TaskContextError):
    """Raised when no user-authored next action has been saved."""


class PauseContext(BaseModel):
    """The minimum durable context needed to resume a task accurately."""

    next_action: str = Field(min_length=1, max_length=1000)
    working_notes: str | None = Field(default=None, max_length=10_000)
    location: dict[str, Any] = Field(default_factory=dict)
    resources: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("next_action")
    @classmethod
    def normalize_next_action(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("next_action cannot be blank")
        return normalized

    @field_validator("working_notes")
    @classmethod
    def normalize_working_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("resources")
    @classmethod
    def normalize_resources(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            resource = value.strip()
            if resource and resource not in normalized:
                normalized.append(resource)
        return normalized


async def get_owned_task(session: AsyncSession, user_id: UUID, task_id: UUID) -> Task:
    result = await session.execute(select(Task).where(Task.id == task_id, Task.user_id == user_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise TaskNotFoundError("Task not found")
    return task


async def start_task(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
    next_action: str | None = None,
) -> Task:
    """Mark a task active and optionally seed its first exact next action."""
    task = await get_owned_task(session, user_id, task_id)
    if task.status in {TaskStatus.DONE, TaskStatus.CANCELLED}:
        raise InvalidTaskTransitionError(f"Cannot start a task with status '{task.status.value}'")

    now = datetime.now(UTC)
    task.status = TaskStatus.IN_PROGRESS
    task.started_at = task.started_at or now

    if next_action is not None:
        normalized = next_action.strip()
        if not normalized:
            raise TaskContextError("next_action cannot be blank")
        snapshot = dict(task.context_snapshot or {})
        snapshot.update(
            {
                "version": 1,
                "next_action": normalized,
                "last_updated": now.isoformat(),
            }
        )
        task.context_snapshot = snapshot

    await session.commit()
    await session.refresh(task)
    return task


async def pause_task(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
    context: PauseContext,
) -> Task:
    """Persist the user's exact stopping point without changing it through AI."""
    task = await get_owned_task(session, user_id, task_id)
    if task.status != TaskStatus.IN_PROGRESS:
        raise InvalidTaskTransitionError("Only an in-progress task can be paused")

    now = datetime.now(UTC)
    task.context_snapshot = {
        "version": 1,
        "next_action": context.next_action,
        "working_notes": context.working_notes,
        "location": context.location,
        "resources": context.resources,
        "paused_at": now.isoformat(),
        "last_updated": now.isoformat(),
    }
    task.interruption_count = (task.interruption_count or 0) + 1
    await session.commit()
    await session.refresh(task)
    return task


async def load_resume_context(
    session: AsyncSession, user_id: UUID, task_id: UUID
) -> dict[str, Any]:
    """Return only persisted context, with its provenance made explicit."""
    task = await get_owned_task(session, user_id, task_id)
    snapshot = dict(task.context_snapshot or {})
    next_action = snapshot.get("next_action")
    if not isinstance(next_action, str) or not next_action.strip():
        raise MissingRecoveryContextError(
            "No saved next action exists for this task; pause it with context first"
        )

    return {
        "task_id": str(task.id),
        "title": task.title,
        "status": task.status.value,
        "resume_step": next_action.strip(),
        "working_notes": snapshot.get("working_notes"),
        "location": snapshot.get("location") or {},
        "resources": snapshot.get("resources") or [],
        "paused_at": snapshot.get("paused_at"),
        "source": "saved_user_context",
    }
