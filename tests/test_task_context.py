"""Tests for durable, model-independent interruption recovery."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from neuro_os.models import EnergyLevel, Task, TaskStatus, User
from neuro_os.task_context import (
    InvalidTaskTransitionError,
    MissingRecoveryContextError,
    PauseContext,
    TaskNotFoundError,
    load_resume_context,
    pause_task,
    start_task,
)


@pytest.mark.asyncio
async def test_pause_context_survives_a_new_database_session(engine):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as first_session:
        user = User(email="resume@example.com", hashed_password="test")
        task = Task(
            user=user,
            title="Implement durable recovery",
            status=TaskStatus.OPEN,
            energy_level=EnergyLevel.DEEP,
        )
        first_session.add_all([user, task])
        await first_session.commit()
        await first_session.refresh(user)
        await first_session.refresh(task)

        await start_task(
            first_session,
            user.id,
            task.id,
            "Open task_context.py and add the pause operation",
        )
        paused = await pause_task(
            first_session,
            user.id,
            task.id,
            PauseContext(
                next_action="Add the ownership test for resume context",
                working_notes="The happy path is already passing.",
                location={"file": "tests/test_task_context.py", "line": 20},
                resources=["docs/context-contract.md", "docs/context-contract.md", ""],
            ),
        )
        user_id = user.id
        task_id = task.id

    async with async_session() as restarted_session:
        recovered = await load_resume_context(restarted_session, user_id, task_id)

    assert paused.interruption_count == 1
    assert recovered == {
        "task_id": str(task_id),
        "title": "Implement durable recovery",
        "status": "in_progress",
        "resume_step": "Add the ownership test for resume context",
        "working_notes": "The happy path is already passing.",
        "location": {"file": "tests/test_task_context.py", "line": 20},
        "resources": ["docs/context-contract.md"],
        "paused_at": paused.context_snapshot["paused_at"],
        "source": "saved_user_context",
    }


@pytest.mark.asyncio
async def test_resume_context_is_scoped_to_task_owner(session):
    owner = User(email="owner@example.com", hashed_password="test")
    other_user = User(email="other@example.com", hashed_password="test")
    task = Task(
        user=owner,
        title="Private task",
        status=TaskStatus.IN_PROGRESS,
        energy_level=EnergyLevel.SHALLOW,
        context_snapshot={"next_action": "Private next step"},
    )
    session.add_all([owner, other_user, task])
    await session.commit()
    await session.refresh(other_user)
    await session.refresh(task)

    with pytest.raises(TaskNotFoundError):
        await load_resume_context(session, other_user.id, task.id)


@pytest.mark.asyncio
async def test_pause_requires_an_in_progress_task(session):
    user = User(email="open-task@example.com", hashed_password="test")
    task = Task(
        user=user,
        title="Not started",
        status=TaskStatus.OPEN,
        energy_level=EnergyLevel.SHALLOW,
    )
    session.add_all([user, task])
    await session.commit()
    await session.refresh(user)
    await session.refresh(task)

    with pytest.raises(InvalidTaskTransitionError, match="in-progress"):
        await pause_task(
            session,
            user.id,
            task.id,
            PauseContext(next_action="This should not be persisted"),
        )


@pytest.mark.asyncio
async def test_resume_requires_a_saved_user_authored_next_action(session):
    user = User(email="missing-context@example.com", hashed_password="test")
    task = Task(
        user=user,
        title="Missing context",
        status=TaskStatus.IN_PROGRESS,
        energy_level=EnergyLevel.DEEP,
    )
    session.add_all([user, task])
    await session.commit()
    await session.refresh(user)
    await session.refresh(task)

    with pytest.raises(MissingRecoveryContextError, match="No saved next action"):
        await load_resume_context(session, user.id, task.id)
