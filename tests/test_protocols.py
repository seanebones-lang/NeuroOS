"""Tests for trustworthy protocol execution."""

import json

import pytest
from sqlalchemy import select

from neuro_os.models import ProtocolRun, ProtocolType, Task, User
from neuro_os.protocols import ProtocolEngine, ProtocolExecutionError


class StepAgent:
    def __init__(self, invalid_blocks: bool = False) -> None:
        self.invalid_blocks = invalid_blocks

    async def run(self, prompt: str, context) -> str:
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
