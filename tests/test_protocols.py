"""Tests for trustworthy protocol execution."""

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from neuro_os.models import Protocol, ProtocolRun, ProtocolType, Task, User
from neuro_os.protocols import (
    ProtocolEngine,
    ProtocolExecutionError,
    ProtocolRunInProgressError,
    daily_idempotency_key,
)


class StepAgent:
    def __init__(self, invalid_blocks: bool = False) -> None:
        self.invalid_blocks = invalid_blocks
        self.calls = 0

    async def run(self, prompt: str, context) -> str:
        self.calls += 1
        if "Step: gather_inputs" in prompt:
            return json.dumps({"open_tasks": [], "calendar_events": []})
        if "Step: assess_energy" in prompt:
            return json.dumps({"energy_blocks": []})
        if "Step: sequence_blocks" in prompt:
            if self.invalid_blocks:
                return "not-json"
            return json.dumps(
                {
                    "blocks": [
                        {
                            "title": "Build the core flow",
                            "energy_level": "deep",
                            "estimated_minutes": 90,
                        },
                        {
                            "title": "Reply to customers",
                            "energy_level": "shallow",
                            "estimated_minutes": 45,
                        },
                        {
                            "title": "Close open loops",
                            "energy_level": "recovery",
                            "estimated_minutes": 30,
                        },
                    ]
                }
            )
        if "Step: present_plan" in prompt:
            return "Three grounded blocks are ready."
        raise AssertionError(f"Unexpected prompt: {prompt}")


async def _create_user(session) -> User:
    user = User(email="protocol@example.com", hashed_password="test")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_morning_protocol_persists_three_validated_tasks(session):
    user = await _create_user(session)
    agent = StepAgent()
    engine = ProtocolEngine(session, None, lambda protocol_type: agent)

    run = await engine.run_protocol(ProtocolType.MORNING, user.id)

    tasks = (
        (await session.execute(select(Task).where(Task.user_id == user.id).order_by(Task.sequence)))
        .scalars()
        .all()
    )
    assert run.status == "completed"
    assert run.tasks_created == 3
    assert [task.title for task in tasks] == [
        "Build the core flow",
        "Reply to customers",
        "Close open loops",
    ]
    assert all(task.protocol_id == run.protocol_id for task in tasks)
    assert all(task.protocol_run_id == run.id for task in tasks)


@pytest.mark.asyncio
async def test_each_morning_run_returns_only_its_own_tasks(session):
    user = await _create_user(session)
    agent = StepAgent()
    engine = ProtocolEngine(session, None, lambda protocol_type: agent)

    first_run = await engine.run_protocol(ProtocolType.MORNING, user.id)
    second_run = await engine.run_protocol(ProtocolType.MORNING, user.id)

    first_tasks = (
        (
            await session.execute(
                select(Task).where(Task.protocol_run_id == first_run.id).order_by(Task.sequence)
            )
        )
        .scalars()
        .all()
    )
    second_tasks = (
        (
            await session.execute(
                select(Task).where(Task.protocol_run_id == second_run.id).order_by(Task.sequence)
            )
        )
        .scalars()
        .all()
    )

    assert len(first_tasks) == 3
    assert len(second_tasks) == 3
    assert {task.id for task in first_tasks}.isdisjoint(task.id for task in second_tasks)
    assert all(task.protocol_id == first_run.protocol_id for task in first_tasks + second_tasks)


@pytest.mark.asyncio
async def test_idempotent_morning_retry_returns_original_run_without_duplicate_tasks(session):
    user = await _create_user(session)
    agent = StepAgent()
    engine = ProtocolEngine(session, None, lambda protocol_type: agent)

    first_run = await engine.run_protocol(
        ProtocolType.MORNING,
        user.id,
        idempotency_key="daily:morning:2026-09-15",
    )
    replayed_run = await engine.run_protocol(
        ProtocolType.MORNING,
        user.id,
        idempotency_key="daily:morning:2026-09-15",
    )

    runs = (await session.execute(select(ProtocolRun))).scalars().all()
    tasks = (await session.execute(select(Task))).scalars().all()
    assert replayed_run.id == first_run.id
    assert replayed_run.idempotency_key == "daily:morning:2026-09-15"
    assert len(runs) == 1
    assert len(tasks) == 3
    assert agent.calls == 4


def test_daily_idempotency_key_uses_the_users_local_date():
    instant = datetime(2026, 9, 16, 1, 0, tzinfo=UTC)

    assert daily_idempotency_key(ProtocolType.MORNING, "America/Chicago", instant) == (
        "daily:morning:2026-09-15"
    )
    assert daily_idempotency_key(ProtocolType.MORNING, "Europe/London", instant) == (
        "daily:morning:2026-09-16"
    )


@pytest.mark.asyncio
async def test_idempotent_request_conflicts_while_original_run_is_active(session):
    user = await _create_user(session)
    protocol = Protocol(
        user_id=user.id,
        name="Morning Protocol",
        type=ProtocolType.MORNING,
        definition={},
        is_default=True,
    )
    session.add(protocol)
    await session.flush()
    active_run = ProtocolRun(
        user_id=user.id,
        protocol_id=protocol.id,
        idempotency_key="active-run",
        status="running",
    )
    session.add(active_run)
    await session.commit()

    agent = StepAgent()
    engine = ProtocolEngine(session, None, lambda protocol_type: agent)

    with pytest.raises(ProtocolRunInProgressError, match="already in progress"):
        await engine.run_protocol(
            ProtocolType.MORNING,
            user.id,
            idempotency_key="active-run",
        )
    assert agent.calls == 0


@pytest.mark.asyncio
async def test_invalid_morning_output_records_failure_without_fabricated_tasks(session):
    user = await _create_user(session)
    agent = StepAgent(invalid_blocks=True)
    engine = ProtocolEngine(session, None, lambda protocol_type: agent)

    with pytest.raises(ProtocolExecutionError, match="invalid JSON"):
        await engine.run_protocol(ProtocolType.MORNING, user.id)

    runs = (await session.execute(select(ProtocolRun))).scalars().all()
    tasks = (await session.execute(select(Task))).scalars().all()
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert "invalid JSON" in runs[0].notes
    assert tasks == []
