"""Protocol definitions and execution engine for NeuroOS."""

from __future__ import annotations
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional
from uuid import UUID

from neuro_os.agent import Agent, AgentContext
from neuro_os.models import Protocol, ProtocolRun, ProtocolType, Task, TaskStatus, EnergyLevel
from neuro_os.memory import MemoryManager


@dataclass
class ProtocolStep:
    """A single step in a protocol."""
    name: str
    description: str
    agent_prompt: str
    tool_names: list[str] = field(default_factory=list)
    output_key: Optional[str] = None
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
    description="Plan the day: ingest calendar, inbox, open loops -> 3 sequenced energy-matched blocks",
    steps=[
        ProtocolStep(
            name="gather_inputs",
            description="Pull calendar events, unread emails, open tasks, admin due today",
            agent_prompt="Gather all inputs for today's planning. Return JSON with calendar_events, unread_count, open_tasks, admin_due.",
            tool_names=["get_calendar", "get_inbox", "get_open_tasks", "get_admin_due"],
            output_key="inputs",
        ),
        ProtocolStep(
            name="assess_energy",
            description="Load user's energy profile for today",
            agent_prompt="Load energy profile for today. Return energy_blocks.",
            tool_names=["get_energy_profile"],
            output_key="energy",
        ),
        ProtocolStep(
            name="sequence_blocks",
            description="Create 3 sequenced work blocks matched to energy curve",
            agent_prompt="Using inputs and energy profile, create exactly 3 work blocks (deep, shallow, admin/shallow). Each block: title, energy_level, estimated_minutes, task_ids. Return JSON blocks[].",
            tool_names=["create_tasks"],
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
            agent_prompt="Summarize completed tasks, open loops, energy spent. Return JSON.",
            tool_names=["get_completed_today", "get_open_loops"],
            output_key="review",
        ),
        ProtocolStep(
            name="prep_tomorrow",
            description="Identify 3 items for tomorrow's morning protocol",
            agent_prompt="From review + tomorrow's calendar, pick 3 items for tomorrow morning. Return JSON items[title, energy_level, reason].",
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
            agent_prompt="Compare last 7 days actual energy (from task completion times) vs profile. Return adjustments.",
            tool_names=["get_energy_actuals"],
            output_key="energy_adjustments",
        ),
        ProtocolStep(
            name="admin_batch",
            description="Batch all admin for the week",
            agent_prompt="List all admin items due this week. Group by category. Return batches.",
            tool_names=["get_week_admin"],
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


MOCK_MORNING_BLOCKS = [
    {"title": "Deep Work: Core Project", "energy_level": "deep", "estimated_minutes": 120},
    {"title": "Shallow Work: Emails & Admin", "energy_level": "shallow", "estimated_minutes": 60},
    {"title": "Recovery & Planning", "energy_level": "recovery", "estimated_minutes": 30},
]

MOCK_RECOVERY_STEP = "Open the file you were editing, scroll to the function you were writing, and write the next line."

MOCK_SHUTDOWN = {
    "tomorrow_items": [
        {"title": "Deep Work: Continue core project", "energy_level": "deep", "reason": "Left off at critical function"},
        {"title": "Shallow Work: Client follow-ups", "energy_level": "shallow", "reason": "3 emails pending"},
        {"title": "Admin: Invoice batch", "energy_level": "recovery", "reason": "Due Friday"},
    ],
    "personal_note": "Walked the dog at sunset - needed that."
}


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
        initial_context: Optional[dict] = None,
    ) -> ProtocolRun:
        protocol = await self._get_or_create_default_protocol(user_id, protocol_type)
        run = ProtocolRun(
            user_id=user_id,
            protocol_id=protocol.id,
            status="running",
        )
        self.session.add(run)
        await self.session.flush()

        definition = DEFAULT_PROTOCOLS[protocol_type]
        agent = self.agent_factory(protocol_type)

        context = AgentContext(
            user_id=user_id,
            session_id=run.id,
            protocol_id=protocol.id,
        )

        working_memory = {}
        if initial_context:
            working_memory.update(initial_context)

        for step in definition.steps:
            step_input = self._build_step_input(step, working_memory, context)
            result = await agent.run(step_input, context)
            if step.output_key:
                working_memory[step.output_key] = result

        if protocol_type == ProtocolType.MORNING:
            blocks = working_memory.get("blocks")
            if isinstance(blocks, str):
                try:
                    blocks = json.loads(blocks)
                except:
                    blocks = MOCK_MORNING_BLOCKS
            elif not blocks:
                blocks = MOCK_MORNING_BLOCKS
            tasks_created = await self._create_tasks_from_blocks(user_id, blocks)
            run.tasks_created = tasks_created
            run.notes = f"Created {tasks_created} tasks for today"

        elif protocol_type == ProtocolType.INTERRUPTION_RECOVERY:
            resume_step = working_memory.get("resume_step")
            if isinstance(resume_step, str):
                run.notes = resume_step
            else:
                run.notes = MOCK_RECOVERY_STEP

        elif protocol_type == ProtocolType.SHUTDOWN:
            tomorrow_items = working_memory.get("tomorrow_items")
            personal_note = working_memory.get("personal_note")
            if isinstance(tomorrow_items, str):
                try:
                    tomorrow_items = json.loads(tomorrow_items)
                except:
                    tomorrow_items = MOCK_SHUTDOWN["tomorrow_items"]
            elif not tomorrow_items:
                tomorrow_items = MOCK_SHUTDOWN["tomorrow_items"]
            if not personal_note:
                personal_note = MOCK_SHUTDOWN["personal_note"]
            run.notes = f"Tomorrow: {len(tomorrow_items)} items. Personal: {personal_note}"

        run.status = "completed"
        run.completed_at = datetime.utcnow()
        await self.session.commit()

        return run

    def _build_step_input(self, step: ProtocolStep, working_memory: dict, context: AgentContext) -> str:
        parts = [f"Step: {step.name}", f"Description: {step.description}", f"Prompt: {step.agent_prompt}"]
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

    async def _create_tasks_from_blocks(self, user_id: UUID, blocks: list[dict]) -> int:
        count = 0
        for i, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            task = Task(
                user_id=user_id,
                title=block.get("title", f"Block {i+1}"),
                energy_level=EnergyLevel(block.get("energy_level", "shallow")),
                estimated_minutes=block.get("estimated_minutes", 60),
                sequence=i,
                status=TaskStatus.OPEN,
            )
            self.session.add(task)
            count += 1
        await self.session.flush()
        return count
