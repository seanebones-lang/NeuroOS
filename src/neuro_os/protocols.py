"""Protocol definitions and execution engine for NeuroOS."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from neuro_os.agent import Agent, AgentContext
from neuro_os.memory import MemoryManager
from neuro_os.models import EnergyLevel, Protocol, ProtocolRun, ProtocolType, Task, TaskStatus


@dataclass
class ProtocolStep:
    """A single step in a protocol."""

    name: str
    description: str
    agent_prompt: str
    tool_names: list[str] = field(default_factory=list)
    output_key: str | None = None
    required: bool = True


@dataclass
class ProtocolDefinition:
    """Full protocol definition."""

    name: str
    type: ProtocolType
    description: str
    steps: list[ProtocolStep]
    entry_conditions: dict = field(default_factory=dict)


MORNING_PROTOCOL = ProtocolDefinition(
    name="Morning Protocol",
    type=ProtocolType.MORNING,
    description="Plan the day: ingest calendar, open tasks, energy profile -> 3 sequenced energy-matched blocks",
    steps=[
        ProtocolStep(
            name="gather_inputs",
            description="Pull calendar events, open tasks, admin due today",
            agent_prompt="Gather all inputs for today's planning. Return JSON with calendar_events, open_tasks, admin_due.",
            tool_names=["get_calendar", "get_open_tasks"],
            output_key="inputs",
        ),
        ProtocolStep(
            name="assess_energy",
            description="Load user's energy profile for today",
            agent_prompt="Load energy profile for today. Return energy_blocks with time ranges and energy levels.",
            tool_names=["get_energy_profile"],
            output_key="energy",
        ),
        ProtocolStep(
            name="sequence_blocks",
            description="Create 3 sequenced work blocks matched to energy curve",
            agent_prompt="Using inputs and energy profile, create exactly 3 work blocks (deep, shallow, recovery). Each block MUST include: title, energy_level (deep/shallow/recovery), estimated_minutes, scheduled_start (ISO format). Return JSON blocks[].",
            tool_names=[],
            output_key="blocks",
        ),
        ProtocolStep(
            name="present_plan",
            description="Present the day's plan to user",
            agent_prompt="Present the 3 blocks clearly. Ask for confirmation or adjustments.",
            output_key="plan",
        ),
    ],
)


INTERRUPTION_RECOVERY_PROTOCOL = ProtocolDefinition(
    name="Interruption Recovery",
    type=ProtocolType.INTERRUPTION_RECOVERY,
    description="Recover context after interruption: what was I doing, what's next",
    steps=[
        ProtocolStep(
            name="load_snapshot",
            description="Load the interrupted task's context snapshot",
            agent_prompt="Load context_snapshot for the interrupted task. Return editor_position, mental_stack, next_micro_step.",
            tool_names=["get_task_context"],
            output_key="snapshot",
        ),
        ProtocolStep(
            name="assess_interruption",
            description="Determine interruption duration and impact",
            agent_prompt="Calculate interruption duration. Assess whether to resume same task or switch.",
            output_key="assessment",
        ),
        ProtocolStep(
            name="generate_resume_step",
            description="Generate exact next step to resume flow",
            agent_prompt="Based on snapshot and assessment, output ONE sentence: the exact next micro-step to resume flow. No questions, no options.",
            output_key="resume_step",
        ),
    ],
)


SHUTDOWN_PROTOCOL = ProtocolDefinition(
    name="Shutdown Protocol",
    type=ProtocolType.SHUTDOWN,
    description="End of day: capture completed, open loops, prep tomorrow",
    steps=[
        ProtocolStep(
            name="review_day",
            description="Review today's completed tasks and open loops",
            agent_prompt="Summarize completed tasks, open loops, energy spent. Return JSON with completed[], open_loops[].",
            tool_names=["get_completed_today", "get_open_loops"],
            output_key="review",
        ),
        ProtocolStep(
            name="prep_tomorrow",
            description="Identify 3 items for tomorrow's morning protocol",
            agent_prompt="From review + tomorrow's calendar, pick 3 items for tomorrow morning. Return JSON items[title, energy_level, reason]. energy_level must be deep/shallow/recovery.",
            tool_names=["get_tomorrow_calendar"],
            output_key="tomorrow_items",
        ),
        ProtocolStep(
            name="personal_note",
            description="Capture one personal reflection",
            agent_prompt="One sentence: what mattered today personally. Not work.",
            output_key="personal_note",
        ),
    ],
)


WEEKLY_REVIEW_PROTOCOL = ProtocolDefinition(
    name="Weekly Review",
    type=ProtocolType.WEEKLY_REVIEW,
    description="Weekly: review energy patterns, adjust profile, batch admin",
    steps=[
        ProtocolStep(
            name="energy_audit",
            description="Analyze actual vs planned energy alignment",
            agent_prompt="Compare last 7 days actual energy (from task completion times) vs profile. Return adjustments as day->time_block->energy_level overrides.",
            tool_names=["get_energy_actuals"],
            output_key="energy_adjustments",
        ),
        ProtocolStep(
            name="admin_batch",
            description="Batch all admin for the week",
            agent_prompt="List all admin items due this week. Group by category. Return batches.",
            tool_names=["get_due_admin"],
            output_key="admin_batches",
        ),
        ProtocolStep(
            name="pattern_adjustment",
            description="Update energy profile for next week",
            agent_prompt="Apply energy adjustments to profile. Return updated weekly_pattern.",
            tool_names=["update_energy_profile"],
            output_key="updated_profile",
        ),
    ],
)


ADMIN_BATCH_PROTOCOL = ProtocolDefinition(
    name="Admin Batch",
    type=ProtocolType.ADMIN_BATCH,
    description="Process recurring admin: invoices, taxes, licenses, follow-ups",
    steps=[
        ProtocolStep(
            name="collect_due",
            description="Collect all admin items due now",
            agent_prompt="Get all active admin items where next_due <= now. Return list.",
            tool_names=["get_due_admin"],
            output_key="due_items",
        ),
        ProtocolStep(
            name="draft_each",
            description="Draft each admin item using template",
            agent_prompt="For each due item, use its draft_template + ai_instructions to produce a draft. Return drafts.",
            tool_names=["draft_admin"],
            output_key="drafts",
        ),
        ProtocolStep(
            name="present_for_review",
            description="Present drafts for user review/approval",
            agent_prompt="Present each draft with approve/edit/skip options.",
            output_key="reviewed",
        ),
    ],
)


COMMS_DRAFT_PROTOCOL = ProtocolDefinition(
    name="Comms Draft",
    type=ProtocolType.COMMS_DRAFT,
    description="Draft awkward/complex communications in user's voice",
    steps=[
        ProtocolStep(
            name="gather_context",
            description="Get recipient info, channel, goal, constraints",
            agent_prompt="Collect all context for this communication. Return JSON.",
            tool_names=["get_recipient_info", "get_user_voice_samples"],
            output_key="context",
        ),
        ProtocolStep(
            name="select_template",
            description="Select or create appropriate template",
            agent_prompt="Match to existing template or create ad-hoc structure. Return template_id or new structure.",
            tool_names=["get_templates"],
            output_key="template",
        ),
        ProtocolStep(
            name="draft_message",
            description="Draft the message in user's voice",
            agent_prompt="Using context + template + voice samples, draft the message. Match tone, brevity, directness. Return draft.",
            output_key="draft",
        ),
        ProtocolStep(
            name="present_draft",
            description="Present draft for review",
            agent_prompt="Show draft with send/edit/regenerate options.",
            output_key="final",
        ),
    ],
)


DEFAULT_PROTOCOLS = {
    ProtocolType.MORNING: MORNING_PROTOCOL,
    ProtocolType.INTERRUPTION_RECOVERY: INTERRUPTION_RECOVERY_PROTOCOL,
    ProtocolType.SHUTDOWN: SHUTDOWN_PROTOCOL,
    ProtocolType.WEEKLY_REVIEW: WEEKLY_REVIEW_PROTOCOL,
    ProtocolType.ADMIN_BATCH: ADMIN_BATCH_PROTOCOL,
    ProtocolType.COMMS_DRAFT: COMMS_DRAFT_PROTOCOL,
}


class ProtocolExecutionError(RuntimeError):
    """Raised when a protocol cannot produce a trustworthy result."""


class ProtocolRunInProgressError(ProtocolExecutionError):
    """Raised when the same idempotent run is already executing."""


class InvalidIdempotencyKeyError(ProtocolExecutionError):
    """Raised when an idempotency key cannot be stored safely."""


MAX_IDEMPOTENCY_KEY_LENGTH = 255


def daily_idempotency_key(
    protocol_type: ProtocolType,
    timezone_name: str,
    now: datetime | None = None,
) -> str:
    """Build the shared daily key used by scheduled and interactive entry points."""
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ProtocolExecutionError(f"Unknown user timezone: {timezone_name}") from exc
    current = now.astimezone(timezone) if now is not None else datetime.now(timezone)
    return f"daily:{protocol_type.value}:{current.date().isoformat()}"


class ProtocolEngine:
    """Executes protocols with agent integration."""

    def __init__(
        self,
        session,
        memory: MemoryManager,
        agent_factory: Callable[[ProtocolType], Agent],
    ):
        self.session = session
        self.memory = memory
        self.agent_factory = agent_factory

    async def run_protocol(
        self,
        protocol_type: ProtocolType,
        user_id: UUID,
        initial_context: dict | None = None,
        idempotency_key: str | None = None,
    ) -> ProtocolRun:
        protocol = await self._get_or_create_default_protocol(user_id, protocol_type)
        protocol_id = protocol.id
        normalized_key = self._normalize_idempotency_key(idempotency_key)
        if normalized_key is not None:
            existing = await self._get_idempotent_run(user_id, protocol_id, normalized_key)
            if existing is not None:
                return self._resolve_idempotent_run(existing)

        run = ProtocolRun(
            user_id=user_id,
            protocol_id=protocol_id,
            idempotency_key=normalized_key,
            status="running",
        )
        self.session.add(run)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            existing = await self._get_idempotent_run(user_id, protocol_id, normalized_key)
            if existing is None:
                raise ProtocolExecutionError("Could not create protocol run") from exc
            return self._resolve_idempotent_run(existing)
        run_id = run.id
        await self.session.commit()

        definition = DEFAULT_PROTOCOLS[protocol_type]

        context = AgentContext(
            user_id=user_id,
            session_id=run.id,
            protocol_id=protocol_id,
        )

        working_memory = {}
        if initial_context:
            working_memory.update(initial_context)

        try:
            for step in definition.steps:
                agent = self.agent_factory(protocol_type)
                step_input = self._build_step_input(step, working_memory)
                result = await agent.run(step_input, context)
                if step.output_key:
                    working_memory[step.output_key] = self._parse_step_result(
                        step.output_key, result
                    )

            if protocol_type == ProtocolType.MORNING:
                blocks = self._validate_morning_blocks(working_memory.get("blocks"))
                tasks_created = await self._create_tasks_from_blocks(
                    user_id, protocol_id, run.id, blocks
                )
                run.tasks_created = tasks_created
                run.notes = f"Created {tasks_created} tasks for today"

            elif protocol_type == ProtocolType.INTERRUPTION_RECOVERY:
                resume_step = working_memory.get("resume_step")
                if not isinstance(resume_step, str) or not resume_step.strip():
                    raise ProtocolExecutionError(
                        "Interruption recovery did not produce a resume step"
                    )
                run.notes = resume_step.strip()

            elif protocol_type == ProtocolType.SHUTDOWN:
                tomorrow_items = working_memory.get("tomorrow_items")
                if not isinstance(tomorrow_items, list):
                    raise ProtocolExecutionError(
                        "Shutdown did not produce a list of tomorrow items"
                    )
                personal_note = (initial_context or {}).get("personal_note")
                note_suffix = (
                    f" Personal: {personal_note.strip()}"
                    if isinstance(personal_note, str) and personal_note.strip()
                    else ""
                )
                run.notes = f"Tomorrow: {len(tomorrow_items)} items.{note_suffix}"

            run.status = "completed"
            run.completed_at = datetime.now(UTC)
            await self.session.commit()
            return run
        except Exception as exc:
            await self.session.rollback()
            failed_run = await self.session.get(ProtocolRun, run_id)
            if failed_run is not None:
                failed_run.status = "failed"
                failed_run.completed_at = datetime.now(UTC)
                failed_run.notes = str(exc)[:2000]
                await self.session.commit()
            if isinstance(exc, ProtocolExecutionError):
                raise
            raise ProtocolExecutionError(f"{definition.name} failed: {exc}") from exc

    @staticmethod
    def _normalize_idempotency_key(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise InvalidIdempotencyKeyError("Idempotency key cannot be blank")
        if len(normalized) > MAX_IDEMPOTENCY_KEY_LENGTH:
            raise InvalidIdempotencyKeyError(
                f"Idempotency key cannot exceed {MAX_IDEMPOTENCY_KEY_LENGTH} characters"
            )
        return normalized

    async def _get_idempotent_run(
        self,
        user_id: UUID,
        protocol_id: UUID,
        idempotency_key: str | None,
    ) -> ProtocolRun | None:
        if idempotency_key is None:
            return None
        return await self.session.scalar(
            select(ProtocolRun).where(
                ProtocolRun.user_id == user_id,
                ProtocolRun.protocol_id == protocol_id,
                ProtocolRun.idempotency_key == idempotency_key,
            )
        )

    @staticmethod
    def _resolve_idempotent_run(run: ProtocolRun) -> ProtocolRun:
        if run.status == "completed":
            return run
        if run.status == "running":
            raise ProtocolRunInProgressError(
                f"Protocol run {run.id} is already in progress for this idempotency key"
            )
        raise ProtocolExecutionError(
            f"Protocol run {run.id} previously failed for this idempotency key: "
            f"{run.notes or 'unknown error'}"
        )

    @staticmethod
    def _parse_step_result(output_key: str, result: str) -> Any:
        if not isinstance(result, str) or not result.strip():
            raise ProtocolExecutionError(f"Step '{output_key}' returned no usable result")
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            if output_key in {"resume_step", "plan", "personal_note", "final", "draft"}:
                return result.strip()
            raise ProtocolExecutionError(f"Step '{output_key}' returned invalid JSON") from None

        if isinstance(parsed, dict):
            if output_key in parsed:
                return parsed[output_key]
            if output_key == "tomorrow_items" and "items" in parsed:
                return parsed["items"]
        return parsed

    @staticmethod
    def _validate_morning_blocks(blocks: Any) -> list[dict]:
        if not isinstance(blocks, list) or len(blocks) != 3:
            raise ProtocolExecutionError("Morning planning must produce exactly three work blocks")
        validated = []
        for index, block in enumerate(blocks, start=1):
            if not isinstance(block, dict):
                raise ProtocolExecutionError(f"Morning block {index} is not an object")
            title = block.get("title")
            energy_level = block.get("energy_level")
            estimated_minutes = block.get("estimated_minutes")
            if not isinstance(title, str) or not title.strip():
                raise ProtocolExecutionError(f"Morning block {index} has no title")
            try:
                EnergyLevel(energy_level)
            except (TypeError, ValueError):
                raise ProtocolExecutionError(
                    f"Morning block {index} has an invalid energy level"
                ) from None
            if (
                not isinstance(estimated_minutes, int)
                or isinstance(estimated_minutes, bool)
                or not 5 <= estimated_minutes <= 480
            ):
                raise ProtocolExecutionError(f"Morning block {index} has an invalid duration")
            validated.append(
                {
                    "title": title.strip(),
                    "energy_level": energy_level,
                    "estimated_minutes": estimated_minutes,
                }
            )
        return validated

    def _build_step_input(self, step: ProtocolStep, working_memory: dict) -> str:
        parts = [
            f"Step: {step.name}",
            f"Description: {step.description}",
            f"Prompt: {step.agent_prompt}",
        ]
        if working_memory:
            parts.append(f"Working memory: {json.dumps(working_memory, default=str)}")
        return "\n\n".join(parts)

    async def _get_or_create_default_protocol(self, user_id: UUID, ptype: ProtocolType) -> Protocol:
        from sqlalchemy import select

        result = await self.session.execute(
            select(Protocol).where(
                Protocol.user_id == user_id,
                Protocol.type == ptype,
                Protocol.is_default == True,
            )
        )
        protocol = result.scalar_one_or_none()
        if not protocol:
            definition = DEFAULT_PROTOCOLS[ptype]
            protocol = Protocol(
                user_id=user_id,
                name=definition.name,
                type=ptype,
                description=definition.description,
                definition={
                    "steps": [
                        {
                            "name": s.name,
                            "description": s.description,
                            "agent_prompt": s.agent_prompt,
                            "tool_names": s.tool_names,
                            "output_key": s.output_key,
                        }
                        for s in definition.steps
                    ]
                },
                is_default=True,
            )
            self.session.add(protocol)
            await self.session.flush()
        return protocol

    async def _create_tasks_from_blocks(
        self,
        user_id: UUID,
        protocol_id: UUID,
        protocol_run_id: UUID,
        blocks: list[dict],
    ) -> int:
        count = 0
        for i, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            task = Task(
                user_id=user_id,
                protocol_id=protocol_id,
                protocol_run_id=protocol_run_id,
                title=block.get("title", f"Block {i + 1}"),
                energy_level=EnergyLevel(block.get("energy_level", "shallow")),
                estimated_minutes=block.get("estimated_minutes", 60),
                sequence=i,
                status=TaskStatus.OPEN,
                scheduled_start=datetime.fromisoformat(block["scheduled_start"])
                if block.get("scheduled_start")
                else None,
            )
            self.session.add(task)
            count += 1
        await self.session.flush()
        return count
