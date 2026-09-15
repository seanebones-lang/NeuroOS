"""Validated task lifecycle, ownership, and recovery operations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from neuro_os.models import EnergyLevel, Protocol, Task, TaskStatus


class TaskServiceError(RuntimeError):
    """Base error for task operations."""


class TaskNotFoundError(TaskServiceError):
    """Raised when a task does not exist or belongs to another user."""


class InvalidTaskTransitionError(TaskServiceError):
    """Raised when a task cannot enter the requested state."""


class MissingRecoveryContextError(TaskServiceError):
    """Raised when no user-authored next action has been saved."""


class InvalidTaskReferenceError(TaskServiceError):
    """Raised when a related record is absent or belongs to another user."""


class InvalidTaskScheduleError(TaskServiceError):
    """Raised when task scheduling fields conflict."""


class TaskHasDependentsError(TaskServiceError):
    """Raised when deleting a task would orphan subtasks."""


class TaskCreateData(BaseModel):
    """Validated fields accepted when a user creates a task."""

    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10_000)
    energy_level: EnergyLevel = EnergyLevel.SHALLOW
    estimated_minutes: int | None = Field(default=None, ge=1, le=1440)
    parent_id: UUID | None = None
    protocol_id: UUID | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("title cannot be blank")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class TaskUpdateData(BaseModel):
    """Validated mutable task fields."""

    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10_000)
    status: TaskStatus | None = None
    energy_level: EnergyLevel | None = None
    estimated_minutes: int | None = Field(default=None, ge=1, le=1440)
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("title cannot be blank")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("scheduled_start", "scheduled_end")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("scheduled timestamps must include a timezone")
        return value


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


ALLOWED_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.OPEN: frozenset(
        {
            TaskStatus.IN_PROGRESS,
            TaskStatus.BLOCKED,
            TaskStatus.DONE,
            TaskStatus.DEFERRED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.IN_PROGRESS: frozenset(
        {
            TaskStatus.OPEN,
            TaskStatus.BLOCKED,
            TaskStatus.DONE,
            TaskStatus.DEFERRED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.BLOCKED: frozenset(
        {
            TaskStatus.OPEN,
            TaskStatus.IN_PROGRESS,
            TaskStatus.DEFERRED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.DEFERRED: frozenset({TaskStatus.OPEN, TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED}),
    TaskStatus.DONE: frozenset({TaskStatus.OPEN}),
    TaskStatus.CANCELLED: frozenset({TaskStatus.OPEN}),
}


async def get_owned_task(session: AsyncSession, user_id: UUID, task_id: UUID) -> Task:
    result = await session.execute(select(Task).where(Task.id == task_id, Task.user_id == user_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise TaskNotFoundError("Task not found")
    return task


async def get_owned_task_for_update(session: AsyncSession, user_id: UUID, task_id: UUID) -> Task:
    result = await session.execute(
        select(Task).where(Task.id == task_id, Task.user_id == user_id).with_for_update()
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise TaskNotFoundError("Task not found")
    return task


async def create_task(session: AsyncSession, user_id: UUID, data: TaskCreateData) -> Task:
    """Create a task after validating every user-owned reference."""
    if data.parent_id is not None:
        parent = await session.scalar(
            select(Task).where(Task.id == data.parent_id, Task.user_id == user_id)
        )
        if parent is None:
            raise InvalidTaskReferenceError("Parent task is not available to this user")

    if data.protocol_id is not None:
        protocol = await session.scalar(
            select(Protocol).where(Protocol.id == data.protocol_id, Protocol.user_id == user_id)
        )
        if protocol is None:
            raise InvalidTaskReferenceError("Protocol is not available to this user")

    task = Task(user_id=user_id, **data.model_dump())
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


def validate_task_transition(current: TaskStatus, requested: TaskStatus) -> None:
    if requested == current:
        return
    if requested not in ALLOWED_TASK_TRANSITIONS[current]:
        raise InvalidTaskTransitionError(
            f"Cannot change task status from '{current.value}' to '{requested.value}'"
        )


def validate_schedule(start: datetime | None, end: datetime | None) -> None:
    if end is not None and start is None:
        raise InvalidTaskScheduleError("scheduled_end requires scheduled_start")
    try:
        invalid_order = start is not None and end is not None and end <= start
    except TypeError as exc:
        raise InvalidTaskScheduleError(
            "scheduled timestamps must use compatible timezones"
        ) from exc
    if invalid_order:
        raise InvalidTaskScheduleError("scheduled_end must be later than scheduled_start")


def apply_task_status(task: Task, requested: TaskStatus, now: datetime) -> None:
    validate_task_transition(task.status, requested)
    if requested == task.status:
        return

    task.status = requested
    if requested == TaskStatus.IN_PROGRESS and task.started_at is None:
        task.started_at = now
    if requested == TaskStatus.DONE:
        task.completed_at = now
    else:
        task.completed_at = None


async def update_task(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
    data: TaskUpdateData,
) -> Task:
    """Update a task only after validating the complete requested state."""
    task = await get_owned_task_for_update(session, user_id, task_id)
    changes = data.model_dump(exclude_unset=True)

    next_start = changes.get("scheduled_start", task.scheduled_start)
    next_end = changes.get("scheduled_end", task.scheduled_end)
    validate_schedule(next_start, next_end)

    requested_status = changes.pop("status", None)
    if requested_status is not None:
        validate_task_transition(task.status, requested_status)

    for field, value in changes.items():
        setattr(task, field, value)
    if requested_status is not None:
        apply_task_status(task, requested_status, datetime.now(UTC))

    await session.commit()
    await session.refresh(task)
    return task


async def delete_task(session: AsyncSession, user_id: UUID, task_id: UUID) -> None:
    """Delete an owned task only when no subtasks would be orphaned."""
    task = await get_owned_task_for_update(session, user_id, task_id)
    child_id = await session.scalar(select(Task.id).where(Task.parent_id == task.id).limit(1))
    if child_id is not None:
        raise TaskHasDependentsError("Delete or reassign this task's subtasks first")
    await session.delete(task)
    await session.commit()


async def start_task(
    session: AsyncSession,
    user_id: UUID,
    task_id: UUID,
    next_action: str | None = None,
) -> Task:
    """Mark a task active and optionally seed its first exact next action."""
    task = await get_owned_task_for_update(session, user_id, task_id)
    if task.status in {TaskStatus.DONE, TaskStatus.CANCELLED}:
        raise InvalidTaskTransitionError(f"Cannot start a task with status '{task.status.value}'")

    now = datetime.now(UTC)
    apply_task_status(task, TaskStatus.IN_PROGRESS, now)

    if next_action is not None:
        normalized = next_action.strip()
        if not normalized:
            raise TaskServiceError("next_action cannot be blank")
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
    task = await get_owned_task_for_update(session, user_id, task_id)
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
