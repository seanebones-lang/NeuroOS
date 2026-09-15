"""Tests for NeuroOS models."""

import pytest
from datetime import datetime
from uuid import UUID, uuid4

from neuro_os.models import (
    User, Task, Protocol, EnergyProfile, AdminItem, CommsTemplate, ProtocolRun,
    EnergyLevel, TaskStatus, ProtocolType,
)


def test_user_creation():
    """Test User model instantiation."""
    user = User(
        email="test@example.com",
        hashed_password="hashed",
        full_name="Test User",
        timezone="America/Chicago",
        is_active=True,
        is_superuser=False,
    )
    assert user.email == "test@example.com"
    assert user.full_name == "Test User"
    assert user.timezone == "America/Chicago"
    assert user.is_active is True


def test_task_creation():
    """Test Task model instantiation."""
    user_id = uuid4()
    task = Task(
        user_id=user_id,
        title="Test Task",
        energy_level=EnergyLevel.DEEP,
        estimated_minutes=90,
        status=TaskStatus.OPEN,
        sequence=0,
    )
    assert task.title == "Test Task"
    assert task.energy_level == EnergyLevel.DEEP
    assert task.status == TaskStatus.OPEN
    assert task.sequence == 0


def test_protocol_creation():
    """Test Protocol model instantiation."""
    user_id = uuid4()
    protocol = Protocol(
        user_id=user_id,
        name="Test Protocol",
        type=ProtocolType.MORNING,
        definition={"steps": []},
        is_default=False,
    )
    assert protocol.name == "Test Protocol"
    assert protocol.type == ProtocolType.MORNING
    assert protocol.is_default is False


def test_energy_profile():
    """Test EnergyProfile model."""
    user_id = uuid4()
    profile = EnergyProfile(
        user_id=user_id,
        weekly_pattern={"mon": [{"start": "06:00", "end": "10:00", "level": "deep"}]},
    )
    assert profile.user_id == user_id
    assert "mon" in profile.weekly_pattern


def test_admin_item():
    """Test AdminItem model."""
    user_id = uuid4()
    item = AdminItem(
        user_id=user_id,
        name="Quarterly Tax",
        category="tax",
        frequency="quarterly",
        next_due=datetime.utcnow(),
        is_active=True,
    )
    assert item.name == "Quarterly Tax"
    assert item.frequency == "quarterly"
    assert item.is_active is True


def test_comms_template():
    """Test CommsTemplate model."""
    user_id = uuid4()
    template = CommsTemplate(
        user_id=user_id,
        name="Client Follow-up",
        channel="email",
        recipient_type="client",
        body_template="Hi {{name}}, following up on...",
    )
    assert template.name == "Client Follow-up"
    assert template.channel == "email"
    assert template.recipient_type == "client"


def test_protocol_run():
    """Test ProtocolRun model."""
    user_id = uuid4()
    protocol_id = uuid4()
    run = ProtocolRun(
        user_id=user_id,
        protocol_id=protocol_id,
        status="running",
        tasks_created=0,
    )
    assert run.user_id == user_id
    assert run.protocol_id == protocol_id
    assert run.status == "running"
    assert run.tasks_created == 0


def test_enums():
    """Test enum values."""
    assert EnergyLevel.DEEP.value == "deep"
    assert EnergyLevel.SHALLOW.value == "shallow"
    assert EnergyLevel.RECOVERY.value == "recovery"

    assert TaskStatus.OPEN.value == "open"
    assert TaskStatus.IN_PROGRESS.value == "in_progress"
    assert TaskStatus.DONE.value == "done"

    assert ProtocolType.MORNING.value == "morning"
    assert ProtocolType.INTERRUPTION_RECOVERY.value == "interruption_recovery"
    assert ProtocolType.SHUTDOWN.value == "shutdown"
