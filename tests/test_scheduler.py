from datetime import timedelta
"""Tests for NeuroOS scheduler."""

import pytest
from datetime import datetime, time
from uuid import uuid4

from neuro_os.scheduler import EnergyAwareScheduler, TimeBlock, BlockType, create_default_energy_profile
from neuro_os.models import Task, EnergyLevel, TaskStatus


def test_default_energy_profile():
    """Test default energy profile creation."""
    profile = create_default_energy_profile()
    assert "weekly_pattern" in profile
    assert "overrides" in profile
    assert "mon" in profile["weekly_pattern"]
    assert len(profile["weekly_pattern"]["mon"]) > 0


def test_get_energy_for_time():
    """Test energy level lookup for a given time."""
    profile = create_default_energy_profile()
    user_id = uuid4()
    scheduler = EnergyAwareScheduler(user_id, profile)

    # Monday 8am should be deep
    dt = datetime(2026, 9, 14, 8, 0)  # Monday
    assert scheduler.get_energy_for_time(dt) == EnergyLevel.DEEP

    # Monday 11am should be shallow
    dt = datetime(2026, 9, 14, 11, 0)
    assert scheduler.get_energy_for_time(dt) == EnergyLevel.SHALLOW

    # Monday 12pm should be recovery
    dt = datetime(2026, 9, 14, 12, 0)
    assert scheduler.get_energy_for_time(dt) == EnergyLevel.RECOVERY


def test_generate_daily_schedule():
    """Test daily schedule generation."""
    profile = create_default_energy_profile()
    user_id = uuid4()
    scheduler = EnergyAwareScheduler(user_id, profile)

    # Create test tasks
    tasks = [
        Task(user_id=user_id, title="Deep Work 1", energy_level=EnergyLevel.DEEP, estimated_minutes=90, sequence=0),
        Task(user_id=user_id, title="Deep Work 2", energy_level=EnergyLevel.DEEP, estimated_minutes=60, sequence=1),
        Task(user_id=user_id, title="Shallow Work 1", energy_level=EnergyLevel.SHALLOW, estimated_minutes=45, sequence=0),
        Task(user_id=user_id, title="Admin 1", energy_level=EnergyLevel.RECOVERY, estimated_minutes=30, sequence=0),
    ]

    date = datetime(2026, 9, 14)  # Monday
    blocks = scheduler.generate_daily_schedule(date, tasks)

    assert len(blocks) > 0
    # Should have at least one deep block
    deep_blocks = [b for b in blocks if b.block_type == BlockType.DEEP_WORK]
    assert len(deep_blocks) > 0


def test_timeblock_duration():
    """Test TimeBlock duration calculation."""
    start = datetime(2026, 9, 14, 8, 0)
    end = datetime(2026, 9, 14, 9, 30)
    block = TimeBlock(start=start, end=end, block_type=BlockType.DEEP_WORK, energy_level=EnergyLevel.DEEP)
    assert block.duration_minutes == 90


def test_reschedule_on_interruption():
    """Test rescheduling after interruption."""
    profile = create_default_energy_profile()
    user_id = uuid4()
    scheduler = EnergyAwareScheduler(user_id, profile)

    interrupted = Task(
        user_id=user_id,
        title="Interrupted Task",
        energy_level=EnergyLevel.DEEP,
        estimated_minutes=90,
        sequence=0,
    )
    interrupted.started_at = datetime.utcnow()

    remaining = [
        Task(user_id=user_id, title="Task 2", energy_level=EnergyLevel.DEEP, estimated_minutes=60, sequence=1),
        Task(user_id=user_id, title="Task 3", energy_level=EnergyLevel.SHALLOW, estimated_minutes=45, sequence=0),
    ]

    new_blocks = scheduler.reschedule_on_interruption(
        interrupted,
        timedelta(minutes=30),
        remaining,
    )

    assert len(new_blocks) > 0
    # First block should be for the interrupted task
    assert new_blocks[0].task_ids[0] == interrupted.id
