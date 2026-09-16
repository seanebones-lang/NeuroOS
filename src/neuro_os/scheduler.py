"""Energy-aware scheduler for NeuroOS."""

from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import Enum
from typing import Optional
from uuid import UUID

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from neuro_os.config import settings
from neuro_os.models import EnergyLevel, Task, AdminItem


class BlockType(Enum):
    DEEP_WORK = "deep_work"
    SHALLOW_WORK = "shallow_work"
    ADMIN = "admin"
    RECOVERY = "recovery"
    BUFFER = "buffer"


@dataclass
class TimeBlock:
    """A scheduled time block with energy alignment."""
    start: datetime
    end: datetime
    block_type: BlockType
    energy_level: EnergyLevel
    task_ids: list[UUID] = None
    title: str = ""
    is_fixed: bool = False  # Calendar event, can't move

    def __post_init__(self):
        if self.task_ids is None:
            self.task_ids = []

    @property
    def duration_minutes(self) -> int:
        return int((self.end - self.start).total_seconds() / 60)


class EnergyAwareScheduler:
    """Schedules tasks according to user's energy profile."""

    def __init__(self, user_id: UUID, energy_profile: dict):
        self.user_id = user_id
        self.energy_profile = energy_profile  # weekly_pattern from EnergyProfile
        self.scheduler = AsyncIOScheduler(timezone=settings.scheduler_timezone)
        self.blocks: list[TimeBlock] = []

    def get_energy_for_time(self, dt: datetime) -> EnergyLevel:
        """Get energy level for a specific datetime."""
        day_key = dt.strftime("%a").lower()[:3]  # mon, tue, etc.
        time_str = dt.strftime("%H:%M")

        pattern = self.energy_profile.get("weekly_pattern", {}).get(day_key, [])
        for block in pattern:
            start = block.get("start", "00:00")
            end = block.get("end", "23:59")
            level = block.get("level", "shallow")
            if start <= time_str < end:
                return EnergyLevel(level)
        return EnergyLevel.SHALLOW

    def generate_daily_schedule(self, date: datetime, tasks: list[Task]) -> list[TimeBlock]:
        """Generate optimal schedule for a day given tasks."""
        blocks = []
        current = date.replace(hour=6, minute=0, second=0, microsecond=0)
        end_of_day = date.replace(hour=22, minute=0, second=0, microsecond=0)

        # Separate tasks by energy requirement
        deep_tasks = [t for t in tasks if t.energy_level == EnergyLevel.DEEP]
        shallow_tasks = [t for t in tasks if t.energy_level == EnergyLevel.SHALLOW]
        admin_tasks = [t for t in tasks if t.energy_level == EnergyLevel.RECOVERY]

        # Sort by priority (sequence, then scheduled_start)
        deep_tasks.sort(key=lambda t: (t.sequence, t.scheduled_start or datetime.max))
        shallow_tasks.sort(key=lambda t: (t.sequence, t.scheduled_start or datetime.max))
        admin_tasks.sort(key=lambda t: (t.sequence, t.scheduled_start or datetime.max))

        # Build blocks following energy curve
        while current < end_of_day:
            energy = self.get_energy_for_time(current)

            if energy == EnergyLevel.DEEP and deep_tasks:
                task = deep_tasks.pop(0)
                duration = task.estimated_minutes or 90
                block_end = min(current + timedelta(minutes=duration), end_of_day)
                blocks.append(TimeBlock(
                    start=current,
                    end=block_end,
                    block_type=BlockType.DEEP_WORK,
                    energy_level=EnergyLevel.DEEP,
                    task_ids=[task.id],
                    title=task.title,
                ))
                current = block_end + timedelta(minutes=15)  # Buffer

            elif energy == EnergyLevel.SHALLOW and shallow_tasks:
                task = shallow_tasks.pop(0)
                duration = task.estimated_minutes or 45
                block_end = min(current + timedelta(minutes=duration), end_of_day)
                blocks.append(TimeBlock(
                    start=current,
                    end=block_end,
                    block_type=BlockType.SHALLOW_WORK,
                    energy_level=EnergyLevel.SHALLOW,
                    task_ids=[task.id],
                    title=task.title,
                ))
                current = block_end + timedelta(minutes=10)

            elif energy == EnergyLevel.RECOVERY:
                # Recovery block
                block_end = min(current + timedelta(minutes=30), end_of_day)
                blocks.append(TimeBlock(
                    start=current,
                    end=block_end,
                    block_type=BlockType.RECOVERY,
                    energy_level=EnergyLevel.RECOVERY,
                    title="Recovery / Break",
                ))
                current = block_end

            else:
                # No tasks for this energy level, advance to next transition
                next_transition = self._next_energy_transition(current)
                if next_transition and next_transition > current:
                    current = next_transition
                else:
                    current += timedelta(hours=1)

        self.blocks = blocks
        return blocks

    def _next_energy_transition(self, current: datetime) -> Optional[datetime]:
        """Find next energy level transition."""
        day_key = current.strftime("%a").lower()[:3]
        pattern = self.energy_profile.get("weekly_pattern", {}).get(day_key, [])

        current_time = current.strftime("%H:%M")
        for i, block in enumerate(pattern):
            start = block.get("start", "00:00")
            if start > current_time:
                return current.replace(
                    hour=int(start[:2]),
                    minute=int(start[3:]),
                    second=0,
                    microsecond=0
                )
        return None

    def schedule_admin_items(self, admin_items: list[AdminItem]) -> list[TimeBlock]:
        """Schedule recurring admin items into appropriate slots."""
        blocks = []
        for item in admin_items:
            if not item.is_active:
                continue
            due = item.next_due
            energy = self.get_energy_for_time(due)

            # Admin goes in RECOVERY or SHALLOW blocks
            block_type = BlockType.ADMIN if energy == EnergyLevel.RECOVERY else BlockType.SHALLOW_WORK
            duration = 30  # default

            blocks.append(TimeBlock(
                start=due,
                end=due + timedelta(minutes=duration),
                block_type=block_type,
                energy_level=energy,
                title=f"Admin: {item.name}",
                is_fixed=True,
            ))
        return blocks

    def reschedule_on_interruption(
        self,
        interrupted_task: Task,
        interruption_duration: timedelta,
        remaining_tasks: list[Task],
    ) -> list[TimeBlock]:
        """Reschedule remaining day after interruption."""
        # Mark interrupted task
        interrupted_task.interruption_count = (interrupted_task.interruption_count or 0) + 1
        interrupted_task.context_snapshot = {
            "interrupted_at": datetime.now(UTC).isoformat(),
            "interruption_duration_minutes": interruption_duration.total_seconds() / 60,
        }

        # Push remaining tasks forward
        new_blocks = []
        current = datetime.now(UTC) + timedelta(minutes=15)  # Recovery buffer

        for task in remaining_tasks:
            if task.id == interrupted_task.id:
                # Resume interrupted task first
                duration = (task.estimated_minutes or 60) - 15  # Assume 15 min done
                if duration < 15:
                    duration = 15
            else:
                duration = task.estimated_minutes or 45

            energy = self.get_energy_for_time(current)
            block_end = current + timedelta(minutes=duration)

            new_blocks.append(TimeBlock(
                start=current,
                end=block_end,
                block_type=BlockType.DEEP_WORK if energy == EnergyLevel.DEEP else BlockType.SHALLOW_WORK,
                energy_level=energy,
                task_ids=[task.id],
                title=task.title,
            ))
            current = block_end + timedelta(minutes=10)

        return new_blocks


def create_default_energy_profile(timezone: str = "America/Chicago") -> dict:
    """Create a sensible default energy profile."""
    return {
        "weekly_pattern": {
            "mon": [
                {"start": "06:00", "end": "10:00", "level": "deep"},
                {"start": "10:00", "end": "12:00", "level": "shallow"},
                {"start": "12:00", "end": "13:00", "level": "recovery"},
                {"start": "13:00", "end": "16:00", "level": "shallow"},
                {"start": "16:00", "end": "18:00", "level": "recovery"},
                {"start": "18:00", "end": "22:00", "level": "shallow"},
            ],
            "tue": [
                {"start": "06:00", "end": "10:00", "level": "deep"},
                {"start": "10:00", "end": "12:00", "level": "shallow"},
                {"start": "12:00", "end": "13:00", "level": "recovery"},
                {"start": "13:00", "end": "16:00", "level": "shallow"},
                {"start": "16:00", "end": "18:00", "level": "recovery"},
                {"start": "18:00", "end": "22:00", "level": "shallow"},
            ],
            "wed": [
                {"start": "06:00", "end": "10:00", "level": "deep"},
                {"start": "10:00", "end": "12:00", "level": "shallow"},
                {"start": "12:00", "end": "13:00", "level": "recovery"},
                {"start": "13:00", "end": "16:00", "level": "shallow"},
                {"start": "16:00", "end": "18:00", "level": "recovery"},
                {"start": "18:00", "end": "22:00", "level": "shallow"},
            ],
            "thu": [
                {"start": "06:00", "end": "10:00", "level": "deep"},
                {"start": "10:00", "end": "12:00", "level": "shallow"},
                {"start": "12:00", "end": "13:00", "level": "recovery"},
                {"start": "13:00", "end": "16:00", "level": "shallow"},
                {"start": "16:00", "end": "18:00", "level": "recovery"},
                {"start": "18:00", "end": "22:00", "level": "shallow"},
            ],
            "fri": [
                {"start": "06:00", "end": "10:00", "level": "deep"},
                {"start": "10:00", "end": "12:00", "level": "shallow"},
                {"start": "12:00", "end": "13:00", "level": "recovery"},
                {"start": "13:00", "end": "15:00", "level": "shallow"},
                {"start": "15:00", "end": "17:00", "level": "recovery"},
            ],
            "sat": [
                {"start": "08:00", "end": "11:00", "level": "deep"},
                {"start": "11:00", "end": "13:00", "level": "recovery"},
                {"start": "13:00", "end": "16:00", "level": "shallow"},
            ],
            "sun": [
                {"start": "09:00", "end": "11:00", "level": "recovery"},
                {"start": "11:00", "end": "14:00", "level": "shallow"},
                {"start": "14:00", "end": "17:00", "level": "recovery"},
            ],
        },
        "overrides": {},
    }
