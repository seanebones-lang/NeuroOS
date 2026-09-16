"""Memory system for NeuroOS - working + long-term + vector."""

from __future__ import annotations
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Optional
from uuid import UUID

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from neuro_os.config import settings
from neuro_os.models import Task, ProtocolRun


@dataclass
class WorkingMemory:
    """Short-term working memory for current session."""
    data: dict = field(default_factory=dict)
    max_items: int = 50

    def set(self, key: str, value: Any) -> None:
        if len(self.data) >= self.max_items:
            # Remove oldest
            oldest = next(iter(self.data))
            del self.data[oldest]
        self.data[key] = {"value": value, "timestamp": datetime.now(UTC).isoformat()}

    def get(self, key: str, default: Any = None) -> Any:
        item = self.data.get(key)
        if item:
            return item["value"]
        return default

    def pop(self, key: str, default: Any = None) -> Any:
        item = self.data.pop(key, None)
        if item:
            return item["value"]
        return default

    def clear(self) -> None:
        self.data.clear()


class LongTermMemory:
    """Vector-backed long-term memory using Redis + pgvector (placeholder)."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.prefix = "neuro:ltm:"

    async def store(
        self,
        user_id: UUID,
        content: str,
        metadata: dict,
        embedding: Optional[list[float]] = None,
    ) -> str:
        """Store a memory with optional embedding."""
        import uuid
        memory_id = str(uuid.uuid4())
        key = f"{self.prefix}{user_id}:{memory_id}"

        data = {
            "id": memory_id,
            "user_id": str(user_id),
            "content": content,
            "metadata": metadata,
            "timestamp": datetime.now(UTC).isoformat(),
            "embedding": embedding,
        }
        await self.redis.set(key, json.dumps(data))
        # Add to user's memory index
        await self.redis.sadd(f"{self.prefix}index:{user_id}", memory_id)
        return memory_id

    async def search(
        self,
        user_id: UUID,
        query: str,
        limit: int = 10,
        embedding: Optional[list[float]] = None,
    ) -> list[dict]:
        """Search memories (placeholder - would use vector similarity)."""
        # For now, return recent memories
        index_key = f"{self.prefix}index:{user_id}"
        memory_ids = await self.redis.smembers(index_key)
        memories = []
        for mid in list(memory_ids)[:limit]:
            key = f"{self.prefix}{user_id}:{mid}"
            data = await self.redis.get(key)
            if data:
                memories.append(json.loads(data))
        return sorted(memories, key=lambda m: m["timestamp"], reverse=True)

    async def consolidate(self, user_id: UUID, session_data: dict) -> None:
        """Consolidate session working memory into long-term."""
        # Extract key facts, decisions, patterns
        await self.store(
            user_id,
            content=json.dumps(session_data),
            metadata={"type": "session_consolidation"},
        )


class MemoryManager:
    """Coordinates working + long-term memory."""

    def __init__(self, session: AsyncSession, redis_client: redis.Redis):
        self.session = session
        self.working = WorkingMemory()
        self.long_term = LongTermMemory(redis_client)

    async def load_context(self, user_id: UUID, task_id: Optional[UUID] = None) -> dict:
        """Load relevant context for agent."""
        context = {
            "working_memory": self.working.data,
            "recent_tasks": [],
            "recent_protocols": [],
            "energy_pattern": {},
        }

        # Load recent tasks
        result = await self.session.execute(
            select(Task)
            .where(Task.user_id == user_id)
            .order_by(Task.updated_at.desc())
            .limit(20)
        )
        context["recent_tasks"] = [
            {
                "id": str(t.id),
                "title": t.title,
                "status": t.status.value,
                "energy_level": t.energy_level.value,
            }
            for t in result.scalars()
        ]

        # Load recent protocol runs
        result = await self.session.execute(
            select(ProtocolRun)
            .where(ProtocolRun.user_id == user_id)
            .order_by(ProtocolRun.started_at.desc())
            .limit(10)
        )
        context["recent_protocols"] = [
            {
                "id": str(p.id),
                "protocol_id": str(p.protocol_id),
                "status": p.status,
                "tasks_created": p.tasks_created,
            }
            for p in result.scalars()
        ]

        return context

    async def save_task_context(self, task_id: UUID, snapshot: dict) -> None:
        """Save interruption recovery snapshot."""
        self.working.set(f"task_context:{task_id}", snapshot)

    async def get_task_context(self, task_id: UUID) -> Optional[dict]:
        """Retrieve interruption recovery snapshot."""
        return self.working.get(f"task_context:{task_id}")
