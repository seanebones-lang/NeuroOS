"""Background worker for NeuroOS - handles scheduled protocols, admin, reminders."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from uuid import UUID

import redis.asyncio as redis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from neuro_os.agent import Agent
from neuro_os.config import settings
from neuro_os.database import AsyncSessionLocal
from neuro_os.memory import MemoryManager
from neuro_os.models import AdminItem, ProtocolType, User
from neuro_os.protocols import ProtocolEngine
from neuro_os.tools import get_tools_for_protocol


class NeuroWorker:
    """Background worker for recurring tasks."""

    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone=settings.scheduler_timezone)
        self.redis_client = None

    async def start(self):
        """Start the worker."""
        self.redis_client = redis.from_url(settings.redis_url, decode_responses=True)

        # Schedule jobs
        self._schedule_jobs()
        self.scheduler.start()
        print("NeuroOS worker started")

    async def stop(self):
        """Stop the worker."""
        self.scheduler.shutdown()
        if self.redis_client:
            await self.redis_client.close()
        print("NeuroOS worker stopped")

    def _schedule_jobs(self):
        """Schedule recurring jobs."""
        # Morning protocol at 6am daily
        self.scheduler.add_job(
            self._run_morning_for_all,
            CronTrigger(hour=6, minute=0),
            id="morning_protocol",
            name="Morning protocol for all users",
        )

        # Prepare due admin drafts once each morning. Completion remains a user action.
        self.scheduler.add_job(
            self._process_due_admin,
            CronTrigger(hour=8, minute=0),
            id="admin_batch",
            name="Process due admin items",
        )

        # Weekly review Friday 4pm
        self.scheduler.add_job(
            self._run_weekly_review_for_all,
            CronTrigger(day_of_week="fri", hour=16, minute=0),
            id="weekly_review",
            name="Weekly review for all users",
        )

    async def _run_morning_for_all(self):
        """Run morning protocol for all active users."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User).where(User.is_active == True))
            users = result.scalars().all()

            for user in users:
                try:
                    await self._run_user_protocol(user.id, ProtocolType.MORNING)
                except Exception as e:
                    print(f"Error running morning for {user.id}: {e}")

    async def _run_weekly_review_for_all(self):
        """Run weekly review for all active users."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User).where(User.is_active == True))
            users = result.scalars().all()

            for user in users:
                try:
                    await self._run_user_protocol(user.id, ProtocolType.WEEKLY_REVIEW)
                except Exception as e:
                    print(f"Error running weekly review for {user.id}: {e}")

    async def _process_due_admin(self):
        """Process due admin items for all users."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User).where(User.is_active == True))
            users = result.scalars().all()

            for user in users:
                try:
                    # Get due admin items
                    admin_result = await session.execute(
                        select(AdminItem).where(
                            AdminItem.user_id == user.id,
                            AdminItem.is_active == True,
                            AdminItem.next_due <= datetime.utcnow(),
                        )
                    )
                    due_items = admin_result.scalars().all()

                    if due_items:
                        await self._run_user_protocol(user.id, ProtocolType.ADMIN_BATCH)

                except Exception as e:
                    print(f"Error processing admin for {user.id}: {e}")

    def _calculate_next_due(self, item: AdminItem) -> datetime:
        """Calculate next due date based on frequency."""
        now = datetime.utcnow()
        if item.frequency == "daily":
            return now + timedelta(days=1)
        elif item.frequency == "weekly":
            return now + timedelta(weeks=1)
        elif item.frequency == "monthly":
            # Same day next month
            month = item.month or now.month + 1
            year = now.year
            if month > 12:
                month = 1
                year += 1
            day = min(item.day_of_month or now.day, 28)  # Safe day
            return datetime(year, month, day)
        elif item.frequency == "quarterly":
            return now + timedelta(days=90)
        elif item.frequency == "yearly":
            return now + timedelta(days=365)
        return now + timedelta(days=1)

    async def _run_user_protocol(self, user_id: UUID, ptype: ProtocolType):
        """Run a protocol for a specific user."""
        async with AsyncSessionLocal() as session:
            memory = MemoryManager(session, self.redis_client)

            def agent_factory(pt: ProtocolType) -> Agent:
                prompts = {
                    ProtocolType.MORNING: "You are the Morning Protocol agent. Output JSON with blocks[title, energy_level, estimated_minutes, task_ids].",
                    ProtocolType.INTERRUPTION_RECOVERY: "You are the Interruption Recovery agent. Output one sentence: exact next micro-step.",
                    ProtocolType.SHUTDOWN: "You are the Shutdown Protocol agent. Output JSON with tomorrow_items + personal_note.",
                    ProtocolType.WEEKLY_REVIEW: "You are the Weekly Review agent. Output energy adjustments + admin batches.",
                    ProtocolType.ADMIN_BATCH: "You are the Admin Batch agent. Output drafts for each admin item.",
                    ProtocolType.COMMS_DRAFT: "You are the Comms Drafter. Output draft message matching user's voice.",
                }
                return Agent(
                    tools=get_tools_for_protocol(pt.value),
                    system_prompt=prompts.get(pt, ""),
                )

            engine = ProtocolEngine(session, memory, agent_factory)
            await engine.run_protocol(ptype, user_id)


async def main():
    """Entry point for worker."""
    worker = NeuroWorker()
    try:
        await worker.start()
        # Keep running
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
