"""Tests for trustworthy protocol execution."""

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from neuro_os.agent import Agent, AgentContext, BaseTool, ToolCall, ToolResult
from neuro_os.models import (
    DailyEnergyCheckIn,
    EnergyLevel,
    Protocol,
    ProtocolRun,
    ProtocolStepRun,
    ProtocolType,
    Task,
    User,
)
from neuro_os.protocols import (
    ProtocolEngine,
    ProtocolExecutionError,
    ProtocolRunInProgressError,
    daily_idempotency_key,
)


class StepAgent:
    def __init__(
        self,
        invalid_blocks: bool = False,
        block_energy_levels: list[str] | None = None,
    ) -> None:
        self.invalid_blocks = invalid_blocks
        self.block_energy_levels = block_energy_levels
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
            if self.block_energy_levels is not None:
                return json.dumps(
                    {
                        "blocks": [
                            {
                                "title": f"Capacity block {index}",
                                "energy_level": energy_level,
                                "estimated_minutes": 30,
                            }
                            for index, energy_level in enumerate(
                                self.block_energy_levels, start=1
                            )
                        ]
                    }
                )
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


class TraceEchoTool(BaseTool):
    name = "trace_echo"
    description = "Return a fixed trace result"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        return ToolResult(tool_call_id="", name=self.name, result={"ok": True})


class ToolUsingStepAgent(Agent):
    def __init__(self) -> None:
        super().__init__([TraceEchoTool()], "test", model="trace-model", max_iterations=2)
        self.requests = 0

    async def _call_llm(self, messages: list[dict], context: AgentContext) -> dict:
        self.requests += 1
        if self.requests == 1:
            return {
                "content": None,
                "tool_calls": [ToolCall(name="trace_echo", arguments={}, id="trace-call")],
            }
        prompt = messages[1]["content"]
        if "Step: gather_inputs" in prompt:
            content = '{"open_tasks":[],"calendar_events":[]}'
        elif "Step: assess_energy" in prompt:
            content = '{"energy_blocks":[]}'
        elif "Step: sequence_blocks" in prompt:
            content = json.dumps(
                {
                    "blocks": [
                        {"title": "One", "energy_level": "deep", "estimated_minutes": 30},
                        {
                            "title": "Two",
                            "energy_level": "shallow",
                            "estimated_minutes": 30,
                        },
                        {
                            "title": "Three",
                            "energy_level": "recovery",
                            "estimated_minutes": 30,
                        },
                    ]
                }
            )
        else:
            content = "Ready"
        return {"content": content, "tool_calls": []}


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
    steps = (
        (
            await session.execute(
                select(ProtocolStepRun)
                .where(ProtocolStepRun.protocol_run_id == run.id)
                .order_by(ProtocolStepRun.step_index)
            )
        )
        .scalars()
        .all()
    )
    assert [step.step_name for step in steps] == [
        "gather_inputs",
        "assess_energy",
        "sequence_blocks",
        "present_plan",
    ]
    assert all(step.status == "completed" for step in steps)
    assert all(step.duration_ms is not None for step in steps)
    assert steps[2].output_summary["parsed_type"] == "list"
    assert steps[2].output_summary["characters"] > 0


@pytest.mark.asyncio
async def test_protocol_trace_records_bounded_tool_activity(session):
    user = await _create_user(session)
    engine = ProtocolEngine(session, None, lambda protocol_type: ToolUsingStepAgent())

    run = await engine.run_protocol(ProtocolType.MORNING, user.id)

    steps = (
        (
            await session.execute(
                select(ProtocolStepRun)
                .where(ProtocolStepRun.protocol_run_id == run.id)
                .order_by(ProtocolStepRun.step_index)
            )
        )
        .scalars()
        .all()
    )
    assert len(steps) == 4
    assert all(step.provider == "openai" for step in steps)
    assert all(step.model == "trace-model" for step in steps)
    assert all(
        step.tool_activity
        == [{"tool_call_id": "trace-call", "name": "trace_echo", "status": "completed"}]
        for step in steps
    )


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
    step_count = await session.scalar(
        select(func.count())
        .select_from(ProtocolStepRun)
        .where(ProtocolStepRun.protocol_run_id == first_run.id)
    )
    assert step_count == 4


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
    steps = (
        (await session.execute(select(ProtocolStepRun).order_by(ProtocolStepRun.step_index)))
        .scalars()
        .all()
    )
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert "invalid JSON" in runs[0].notes
    assert tasks == []
    assert [step.status for step in steps] == ["completed", "completed", "failed"]
    assert steps[-1].step_name == "sequence_blocks"
    assert steps[-1].error_category == "ProtocolExecutionError"
    assert "invalid JSON" in steps[-1].error_message


@pytest.mark.asyncio
async def test_morning_plan_cannot_exceed_the_users_stated_capacity(session):
    user = await _create_user(session)
    session.add(
        DailyEnergyCheckIn(
            user_id=user.id,
            check_in_date=datetime.now(ZoneInfo(user.timezone)).date(),
            energy_level=EnergyLevel.RECOVERY,
        )
    )
    await session.commit()

    engine = ProtocolEngine(session, None, lambda protocol_type: StepAgent())
    with pytest.raises(ProtocolExecutionError, match="exceeds the user's stated recovery capacity"):
        await engine.run_protocol(ProtocolType.MORNING, user.id)

    run = (await session.execute(select(ProtocolRun))).scalar_one()
    tasks = (await session.execute(select(Task))).scalars().all()
    assert run.status == "failed"
    assert tasks == []


@pytest.mark.asyncio
async def test_morning_plan_allows_recovery_blocks_for_recovery_capacity(session):
    user = await _create_user(session)
    session.add(
        DailyEnergyCheckIn(
            user_id=user.id,
            check_in_date=datetime.now(ZoneInfo(user.timezone)).date(),
            energy_level=EnergyLevel.RECOVERY,
        )
    )
    await session.commit()

    recovery_blocks = [EnergyLevel.RECOVERY.value] * 3
    engine = ProtocolEngine(
        session,
        None,
        lambda _protocol_type: StepAgent(block_energy_levels=recovery_blocks),
    )
    run = await engine.run_protocol(ProtocolType.MORNING, user.id)
    tasks = (await session.execute(select(Task))).scalars().all()

    assert run.status == "completed"
    assert [task.energy_level for task in tasks] == [EnergyLevel.RECOVERY] * 3
