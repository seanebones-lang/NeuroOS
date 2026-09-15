"""Tests for task ownership, lifecycle, scheduling, and deletion rules."""

from datetime import UTC, datetime, timedelta

import pytest

from neuro_os.models import EnergyLevel, Protocol, ProtocolType, Task, TaskStatus, User
from neuro_os.task_service import (
    InvalidTaskReferenceError,
    InvalidTaskScheduleError,
    InvalidTaskTransitionError,
    TaskCreateData,
    TaskHasDependentsError,
    TaskUpdateData,
    create_task,
    delete_task,
    update_task,
)


async def create_user(session, email: str) -> User:
    user = User(email=email, hashed_password="test")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_create_task_accepts_only_owned_references(session):
    owner = await create_user(session, "task-owner@example.com")
    other = await create_user(session, "other-owner@example.com")
    parent = Task(
        user_id=owner.id,
        title="Owned parent",
        energy_level=EnergyLevel.SHALLOW,
    )
    protocol = Protocol(
        user_id=owner.id,
        name="Owned protocol",
        type=ProtocolType.MORNING,
        definition={},
    )
    foreign_parent = Task(
        user_id=other.id,
        title="Foreign parent",
        energy_level=EnergyLevel.SHALLOW,
    )
    foreign_protocol = Protocol(
        user_id=other.id,
        name="Foreign protocol",
        type=ProtocolType.MORNING,
        definition={},
    )
    session.add_all([parent, protocol, foreign_parent, foreign_protocol])
    await session.commit()
    for record in (parent, protocol, foreign_parent, foreign_protocol):
        await session.refresh(record)

    task = await create_task(
        session,
        owner.id,
        TaskCreateData(
            title="  Child task  ",
            description="  Concrete state  ",
            parent_id=parent.id,
            protocol_id=protocol.id,
        ),
    )

    assert task.title == "Child task"
    assert task.description == "Concrete state"
    assert task.parent_id == parent.id
    assert task.protocol_id == protocol.id

    with pytest.raises(InvalidTaskReferenceError, match="Parent task"):
        await create_task(
            session,
            owner.id,
            TaskCreateData(title="Invalid parent", parent_id=foreign_parent.id),
        )
    with pytest.raises(InvalidTaskReferenceError, match="Protocol"):
        await create_task(
            session,
            owner.id,
            TaskCreateData(title="Invalid protocol", protocol_id=foreign_protocol.id),
        )


@pytest.mark.asyncio
async def test_task_lifecycle_sets_and_clears_timestamps(session):
    user = await create_user(session, "lifecycle@example.com")
    task = await create_task(session, user.id, TaskCreateData(title="Lifecycle task"))

    task = await update_task(
        session,
        user.id,
        task.id,
        TaskUpdateData(status=TaskStatus.IN_PROGRESS),
    )
    assert task.started_at is not None
    assert task.completed_at is None

    task = await update_task(
        session,
        user.id,
        task.id,
        TaskUpdateData(status=TaskStatus.DONE),
    )
    assert task.completed_at is not None

    with pytest.raises(InvalidTaskTransitionError, match="done.*in_progress"):
        await update_task(
            session,
            user.id,
            task.id,
            TaskUpdateData(status=TaskStatus.IN_PROGRESS),
        )

    task = await update_task(
        session,
        user.id,
        task.id,
        TaskUpdateData(status=TaskStatus.OPEN),
    )
    assert task.status == TaskStatus.OPEN
    assert task.completed_at is None


@pytest.mark.asyncio
async def test_task_schedule_is_validated_before_mutation(session):
    user = await create_user(session, "schedule@example.com")
    task = await create_task(session, user.id, TaskCreateData(title="Scheduled task"))
    start = datetime.now(UTC) + timedelta(hours=2)

    with pytest.raises(InvalidTaskScheduleError, match="later"):
        await update_task(
            session,
            user.id,
            task.id,
            TaskUpdateData(scheduled_start=start, scheduled_end=start - timedelta(minutes=1)),
        )

    assert task.scheduled_start is None
    assert task.scheduled_end is None


@pytest.mark.asyncio
async def test_delete_rejects_a_task_with_subtasks(session):
    user = await create_user(session, "subtasks@example.com")
    parent = await create_task(session, user.id, TaskCreateData(title="Parent"))
    child = await create_task(
        session,
        user.id,
        TaskCreateData(title="Child", parent_id=parent.id),
    )

    with pytest.raises(TaskHasDependentsError, match="subtasks"):
        await delete_task(session, user.id, parent.id)

    await delete_task(session, user.id, child.id)
    await delete_task(session, user.id, parent.id)
