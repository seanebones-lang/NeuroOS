"""Tool implementations for NeuroOS protocols."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from neuro_os.agent import AgentContext, BaseTool, ToolResult
from neuro_os.models import EnergyLevel, Task, TaskStatus


class GetOpenTasksTool(BaseTool):
    """Get all open tasks for the user."""

    name = "get_open_tasks"
    description = "Get all open tasks for the current user"
    parameters = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 50},
        },
        "required": [],
    }

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        from sqlalchemy import select

        from neuro_os.database import AsyncSessionLocal

        limit = arguments.get("limit", 50)

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Task)
                .where(Task.user_id == context.user_id, Task.status == TaskStatus.OPEN)
                .order_by(Task.sequence, Task.scheduled_start.nulls_last())
                .limit(limit)
            )
            tasks = result.scalars().all()

            return ToolResult(
                tool_call_id="",
                name=self.name,
                result=[
                    {
                        "id": str(t.id),
                        "title": t.title,
                        "description": t.description,
                        "energy_level": t.energy_level.value,
                        "estimated_minutes": t.estimated_minutes,
                        "scheduled_start": t.scheduled_start.isoformat()
                        if t.scheduled_start
                        else None,
                        "status": t.status.value,
                    }
                    for t in tasks
                ],
            )


class GetEnergyProfileTool(BaseTool):
    """Get the user's energy profile for today."""

    name = "get_energy_profile"
    description = "Get the current user's energy profile for today's date"
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        from sqlalchemy import select

        from neuro_os.database import AsyncSessionLocal
        from neuro_os.models import DailyEnergyCheckIn, EnergyProfile, User

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(EnergyProfile).where(EnergyProfile.user_id == context.user_id)
            )
            profile = result.scalar_one_or_none()

            user = await session.get(User, context.user_id)
            timezone = user.timezone if user is not None else "UTC"
            try:
                check_in_date = datetime.now(ZoneInfo(timezone)).date()
            except ZoneInfoNotFoundError:
                check_in_date = datetime.now(ZoneInfo("UTC")).date()
            check_in = await session.scalar(
                select(DailyEnergyCheckIn).where(
                    DailyEnergyCheckIn.user_id == context.user_id,
                    DailyEnergyCheckIn.check_in_date == check_in_date,
                )
            )

            if not profile:
                from neuro_os.scheduler import create_default_energy_profile

                pattern = create_default_energy_profile("America/Chicago")["weekly_pattern"]
            else:
                pattern = profile.weekly_pattern

            today = check_in_date.strftime("%a").lower()
            today_schedule = pattern.get(today, [])

            return ToolResult(
                tool_call_id="",
                name=self.name,
                result={
                    "today_schedule": today_schedule,
                    "full_pattern": pattern,
                    "stated_capacity": check_in.energy_level.value if check_in else None,
                },
            )


class CreateTasksTool(BaseTool):
    """Create tasks from morning protocol blocks."""

    name = "create_tasks"
    description = "Create tasks from morning protocol output blocks"
    parameters = {
        "type": "object",
        "properties": {
            "blocks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "energy_level": {"type": "string", "enum": ["deep", "shallow", "recovery"]},
                        "estimated_minutes": {"type": "integer"},
                        "scheduled_start": {"type": "string", "format": "date-time"},
                    },
                    "required": ["title", "energy_level", "estimated_minutes"],
                },
            },
        },
        "required": ["blocks"],
    }

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        from neuro_os.database import AsyncSessionLocal
        from neuro_os.models import Task

        blocks = arguments.get("blocks", [])
        created = []

        async with AsyncSessionLocal() as session:
            for i, block in enumerate(blocks):
                task = Task(
                    user_id=context.user_id,
                    protocol_id=context.protocol_id,
                    title=block["title"],
                    energy_level=EnergyLevel(block["energy_level"]),
                    estimated_minutes=block["estimated_minutes"],
                    sequence=i,
                    status=TaskStatus.OPEN,
                    scheduled_start=datetime.fromisoformat(block["scheduled_start"])
                    if block.get("scheduled_start")
                    else None,
                )
                session.add(task)
                await session.flush()
                created.append(
                    {
                        "id": str(task.id),
                        "title": task.title,
                        "energy_level": task.energy_level.value,
                        "estimated_minutes": task.estimated_minutes,
                    }
                )
            await session.commit()

        return ToolResult(
            tool_call_id="",
            name=self.name,
            result={"created": created, "count": len(created)},
        )


class GetTaskContextTool(BaseTool):
    """Get context snapshot for a task (for recovery)."""

    name = "get_task_context"
    description = "Get the context snapshot for a specific task"
    parameters = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "format": "uuid"},
        },
        "required": ["task_id"],
    }

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        from sqlalchemy import select

        from neuro_os.database import AsyncSessionLocal
        from neuro_os.models import Task

        task_id = UUID(arguments["task_id"])

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Task).where(Task.id == task_id, Task.user_id == context.user_id)
            )
            task = result.scalar_one_or_none()

            if not task:
                return ToolResult(
                    tool_call_id="",
                    name=self.name,
                    result=None,
                    error=f"Task {task_id} not found",
                )

            return ToolResult(
                tool_call_id="",
                name=self.name,
                result={
                    "task_id": str(task.id),
                    "title": task.title,
                    "context_snapshot": task.context_snapshot,
                    "started_at": task.started_at.isoformat() if task.started_at else None,
                    "interruption_count": task.interruption_count,
                },
            )


class SaveTaskContextTool(BaseTool):
    """Save context snapshot for a task (when starting or during work)."""

    name = "save_task_context"
    description = "Save context snapshot for the current task"
    parameters = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "format": "uuid"},
            "context": {"type": "object"},
        },
        "required": ["task_id", "context"],
    }

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        from sqlalchemy import select

        from neuro_os.database import AsyncSessionLocal
        from neuro_os.models import Task

        task_id = UUID(arguments["task_id"])
        new_context = arguments["context"]

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Task).where(Task.id == task_id, Task.user_id == context.user_id)
            )
            task = result.scalar_one_or_none()

            if not task:
                return ToolResult(
                    tool_call_id="",
                    name=self.name,
                    result=None,
                    error=f"Task {task_id} not found",
                )

            # Merge with existing snapshot
            existing = task.context_snapshot or {}
            existing.update(new_context)
            existing["last_updated"] = datetime.utcnow().isoformat()

            task.context_snapshot = existing
            if not task.started_at:
                task.started_at = datetime.utcnow()

            await session.commit()

            return ToolResult(
                tool_call_id="",
                name=self.name,
                result={"saved": True, "task_id": str(task.id)},
            )


class GetCalendarTool(BaseTool):
    """Get calendar events for today from a local file."""

    name = "get_calendar"
    description = "Get today's calendar events from local calendar file"
    parameters = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "format": "date"},
        },
        "required": [],
    }

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        from pathlib import Path

        date_str = arguments.get("date") or datetime.now().strftime("%Y-%m-%d")

        # Look for calendar file in user's neuro-os directory
        calendar_dir = Path.home() / ".neuro-os" / "calendar"
        calendar_file = calendar_dir / f"{date_str}.json"

        if calendar_file.exists():
            try:
                with open(calendar_file) as f:
                    events = json.load(f)
                return ToolResult(
                    tool_call_id="",
                    name=self.name,
                    result={"events": events, "source": "file"},
                )
            except Exception as e:
                return ToolResult(
                    tool_call_id="",
                    name=self.name,
                    result={"events": [], "error": f"Failed to parse calendar: {e}"},
                )

        # Return empty if no file
        return ToolResult(
            tool_call_id="",
            name=self.name,
            result={"events": [], "source": "none"},
        )


class GetCompletedTodayTool(BaseTool):
    """Get tasks completed today."""

    name = "get_completed_today"
    description = "Get all tasks completed today for the user"
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        from sqlalchemy import select

        from neuro_os.database import AsyncSessionLocal
        from neuro_os.models import Task, TaskStatus

        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Task)
                .where(
                    Task.user_id == context.user_id,
                    Task.status == TaskStatus.DONE,
                    Task.completed_at >= today_start,
                )
                .order_by(Task.completed_at.desc())
            )
            tasks = result.scalars().all()

            return ToolResult(
                tool_call_id="",
                name=self.name,
                result=[
                    {
                        "id": str(t.id),
                        "title": t.title,
                        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                        "actual_minutes": t.actual_minutes,
                    }
                    for t in tasks
                ],
            )


class GetOpenLoopsTool(BaseTool):
    """Get open tasks and incomplete items."""

    name = "get_open_loops"
    description = "Get all open loops (in_progress, blocked, open tasks)"
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        from sqlalchemy import select

        from neuro_os.database import AsyncSessionLocal
        from neuro_os.models import Task, TaskStatus

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Task)
                .where(
                    Task.user_id == context.user_id,
                    Task.status.in_([TaskStatus.OPEN, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED]),
                )
                .order_by(Task.sequence, Task.scheduled_start.nulls_last())
            )
            tasks = result.scalars().all()

            return ToolResult(
                tool_call_id="",
                name=self.name,
                result=[
                    {
                        "id": str(t.id),
                        "title": t.title,
                        "status": t.status.value,
                        "energy_level": t.energy_level.value,
                    }
                    for t in tasks
                ],
            )


class GetTomorrowCalendarTool(BaseTool):
    """Get tomorrow's calendar events."""

    name = "get_tomorrow_calendar"
    description = "Get tomorrow's calendar events from local calendar file"
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        from pathlib import Path

        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        calendar_dir = Path.home() / ".neuro-os" / "calendar"
        calendar_file = calendar_dir / f"{tomorrow}.json"

        if calendar_file.exists():
            try:
                with open(calendar_file) as f:
                    events = json.load(f)
                return ToolResult(
                    tool_call_id="",
                    name=self.name,
                    result={"events": events, "date": tomorrow},
                )
            except Exception:
                pass

        return ToolResult(
            tool_call_id="",
            name=self.name,
            result={"events": [], "date": tomorrow},
        )


class GetDueAdminTool(BaseTool):
    """Get admin items due now."""

    name = "get_due_admin"
    description = "Get all admin items due now or overdue"
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        from sqlalchemy import select

        from neuro_os.database import AsyncSessionLocal
        from neuro_os.models import AdminItem

        now = datetime.utcnow()

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(AdminItem)
                .where(
                    AdminItem.user_id == context.user_id,
                    AdminItem.is_active == True,
                    AdminItem.next_due <= now,
                )
                .order_by(AdminItem.next_due)
            )
            items = result.scalars().all()

            return ToolResult(
                tool_call_id="",
                name=self.name,
                result=[
                    {
                        "id": str(i.id),
                        "name": i.name,
                        "category": i.category,
                        "description": i.description,
                        "draft_template": i.draft_template,
                        "ai_instructions": i.ai_instructions,
                        "next_due": i.next_due.isoformat(),
                    }
                    for i in items
                ],
            )


class DraftAdminTool(BaseTool):
    """Draft admin item using template."""

    name = "draft_admin"
    description = "Draft an admin item using its template and instructions"
    parameters = {
        "type": "object",
        "properties": {
            "admin_item_id": {"type": "string", "format": "uuid"},
        },
        "required": ["admin_item_id"],
    }

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        from sqlalchemy import select

        from neuro_os.database import AsyncSessionLocal
        from neuro_os.models import AdminItem

        item_id = UUID(arguments["admin_item_id"])

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(AdminItem).where(
                    AdminItem.id == item_id, AdminItem.user_id == context.user_id
                )
            )
            item = result.scalar_one_or_none()

            if not item:
                return ToolResult(
                    tool_call_id="",
                    name=self.name,
                    result=None,
                    error=f"Admin item {item_id} not found",
                )

            # For now, return the template as-is. LLM enhancement can come later.
            draft = item.draft_template or f"Draft for {item.name}"

            return ToolResult(
                tool_call_id="",
                name=self.name,
                result={
                    "admin_item_id": str(item.id),
                    "draft": draft,
                    "category": item.category,
                },
            )


class GetRecipientInfoTool(BaseTool):
    """Get recipient info for comms draft."""

    name = "get_recipient_info"
    description = "Get recipient information from memory or contacts"
    parameters = {
        "type": "object",
        "properties": {
            "recipient": {"type": "string"},
        },
        "required": ["recipient"],
    }

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        # Placeholder - would integrate with contacts/memory
        return ToolResult(
            tool_call_id="",
            name=self.name,
            result={
                "recipient": arguments["recipient"],
                "known": False,
                "notes": "No contact info stored yet",
            },
        )


class GetUserVoiceSamplesTool(BaseTool):
    """Get user's voice samples from sent communications."""

    name = "get_user_voice_samples"
    description = "Get user's writing style samples from sent emails/messages"
    parameters = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 5},
        },
        "required": [],
    }

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        # Placeholder - would query sent emails
        return ToolResult(
            tool_call_id="",
            name=self.name,
            result={
                "samples": [],
                "note": "Voice samples not yet implemented",
            },
        )


class GetTemplatesTool(BaseTool):
    """Get communication templates."""

    name = "get_templates"
    description = "Get user's communication templates"
    parameters = {
        "type": "object",
        "properties": {
            "channel": {"type": "string"},
        },
        "required": [],
    }

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        from sqlalchemy import select

        from neuro_os.database import AsyncSessionLocal
        from neuro_os.models import CommsTemplate

        channel = arguments.get("channel")

        async with AsyncSessionLocal() as session:
            query = select(CommsTemplate).where(CommsTemplate.user_id == context.user_id)
            if channel:
                query = query.where(CommsTemplate.channel == channel)
            result = await session.execute(query)
            templates = result.scalars().all()

            return ToolResult(
                tool_call_id="",
                name=self.name,
                result=[
                    {
                        "id": str(t.id),
                        "name": t.name,
                        "channel": t.channel,
                        "recipient_type": t.recipient_type,
                        "subject_template": t.subject_template,
                        "body_template": t.body_template,
                    }
                    for t in templates
                ],
            )


class GetEnergyActualsTool(BaseTool):
    """Get actual energy data from task completion times."""

    name = "get_energy_actuals"
    description = "Get actual energy alignment data from last 7 days"
    parameters = {
        "type": "object",
        "properties": {
            "days": {"type": "integer", "default": 7},
        },
        "required": [],
    }

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        from sqlalchemy import select

        from neuro_os.database import AsyncSessionLocal
        from neuro_os.models import Task, TaskStatus

        days = arguments.get("days", 7)
        since = datetime.utcnow() - timedelta(days=days)

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Task)
                .where(
                    Task.user_id == context.user_id,
                    Task.status == TaskStatus.DONE,
                    Task.completed_at >= since,
                )
                .order_by(Task.completed_at)
            )
            tasks = result.scalars().all()

            # Group by hour of day
            by_hour = {}
            for t in tasks:
                if t.completed_at:
                    hour = t.completed_at.hour
                    if hour not in by_hour:
                        by_hour[hour] = {"deep": 0, "shallow": 0, "recovery": 0}
                    by_hour[hour][t.energy_level.value] += 1

            return ToolResult(
                tool_call_id="",
                name=self.name,
                result={
                    "completed_count": len(tasks),
                    "by_hour": by_hour,
                    "since": since.isoformat(),
                },
            )


class UpdateEnergyProfileTool(BaseTool):
    """Update user's energy profile with small adjustments."""

    name = "update_energy_profile"
    description = "Update the user's energy profile with small overrides"
    parameters = {
        "type": "object",
        "properties": {
            "adjustments": {
                "type": "object",
                "description": "Day -> {time_block -> energy_level} overrides",
            },
        },
        "required": ["adjustments"],
    }

    async def execute(self, arguments: dict, context: AgentContext) -> ToolResult:
        from sqlalchemy import select

        from neuro_os.database import AsyncSessionLocal
        from neuro_os.models import EnergyProfile

        adjustments = arguments.get("adjustments", {})

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(EnergyProfile).where(EnergyProfile.user_id == context.user_id)
            )
            profile = result.scalar_one_or_none()

            if not profile:
                return ToolResult(
                    tool_call_id="",
                    name=self.name,
                    result=None,
                    error="No energy profile found",
                )

            # Merge adjustments
            overrides = profile.overrides or {}
            for day, day_adjustments in adjustments.items():
                if day not in overrides:
                    overrides[day] = {}
                overrides[day].update(day_adjustments)

            profile.overrides = overrides
            await session.commit()

            return ToolResult(
                tool_call_id="",
                name=self.name,
                result={"updated": True, "overrides": overrides},
            )


# Tool registry for easy access
ALL_TOOLS = [
    GetOpenTasksTool(),
    GetEnergyProfileTool(),
    CreateTasksTool(),
    GetTaskContextTool(),
    SaveTaskContextTool(),
    GetCalendarTool(),
    GetCompletedTodayTool(),
    GetOpenLoopsTool(),
    GetTomorrowCalendarTool(),
    GetDueAdminTool(),
    DraftAdminTool(),
    GetRecipientInfoTool(),
    GetUserVoiceSamplesTool(),
    GetTemplatesTool(),
    GetEnergyActualsTool(),
    UpdateEnergyProfileTool(),
]

TOOLS_BY_NAME = {t.name: t for t in ALL_TOOLS}


def get_tools_for_protocol(protocol_type: str) -> list[BaseTool]:
    """Get the appropriate tools for a protocol type."""
    mapping = {
        "morning": [
            "get_calendar",
            "get_open_tasks",
            "get_energy_profile",
        ],
        "interruption_recovery": [
            "get_task_context",
        ],
        "shutdown": [
            "get_completed_today",
            "get_open_loops",
            "get_tomorrow_calendar",
        ],
        "weekly_review": [
            "get_energy_actuals",
            "get_due_admin",
            "update_energy_profile",
        ],
        "admin_batch": [
            "get_due_admin",
            "draft_admin",
        ],
        "comms_draft": [
            "get_recipient_info",
            "get_user_voice_samples",
            "get_templates",
        ],
    }

    tool_names = mapping.get(protocol_type, [])
    return [TOOLS_BY_NAME[name] for name in tool_names if name in TOOLS_BY_NAME]
