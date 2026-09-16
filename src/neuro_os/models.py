"""SQLAlchemy models for NeuroOS."""

from datetime import date, datetime
from enum import Enum as PyEnum
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class EnergyLevel(PyEnum):
    DEEP = "deep"
    SHALLOW = "shallow"
    RECOVERY = "recovery"


class TaskStatus(PyEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    DEFERRED = "deferred"
    CANCELLED = "cancelled"


class ProtocolType(PyEnum):
    MORNING = "morning"
    INTERRUPTION_RECOVERY = "interruption_recovery"
    SHUTDOWN = "shutdown"
    WEEKLY_REVIEW = "weekly_review"
    ADMIN_BATCH = "admin_batch"
    COMMS_DRAFT = "comms_draft"


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Chicago", nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tasks: Mapped[list["Task"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    protocols: Mapped[list["Protocol"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    energy_profile: Mapped[Optional["EnergyProfile"]] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    energy_check_ins: Mapped[list["DailyEnergyCheckIn"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    comms_templates: Mapped[list["CommsTemplate"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    admin_items: Mapped[list["AdminItem"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class EnergyProfile(Base):
    __tablename__ = "energy_profiles"

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    weekly_pattern: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    overrides: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="energy_profile")


class DailyEnergyCheckIn(Base):
    """A user's stated capacity for one local calendar day."""

    __tablename__ = "daily_energy_check_ins"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False)
    check_in_date: Mapped[date] = mapped_column(nullable=False)
    energy_level: Mapped[EnergyLevel] = mapped_column(Enum(EnergyLevel), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="energy_check_ins")

    __table_args__ = (
        UniqueConstraint("user_id", "check_in_date", name="uq_daily_energy_check_ins_user_date"),
    )


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False)
    parent_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("tasks.id"), index=True, nullable=True)
    protocol_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("protocols.id"), index=True, nullable=True)
    protocol_run_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("protocol_runs.id"), index=True, nullable=True
    )

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.OPEN, index=True, nullable=False)
    energy_level: Mapped[EnergyLevel] = mapped_column(Enum(EnergyLevel), default=EnergyLevel.SHALLOW, nullable=False)

    sequence: Mapped[int] = mapped_column(default=0, nullable=False)
    depends_on: Mapped[list[UUID]] = mapped_column(JSON, default=list, nullable=False)

    estimated_minutes: Mapped[Optional[int]] = mapped_column(nullable=True)
    actual_minutes: Mapped[Optional[int]] = mapped_column(nullable=True)

    scheduled_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    scheduled_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    context_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, default=dict, nullable=False)
    interruption_count: Mapped[int] = mapped_column(default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="tasks")
    parent: Mapped[Optional["Task"]] = relationship(remote_side=[id], backref="subtasks")
    protocol: Mapped[Optional["Protocol"]] = relationship(back_populates="tasks")
    protocol_run: Mapped[Optional["ProtocolRun"]] = relationship(back_populates="tasks")

    __table_args__ = (
        Index("ix_tasks_user_status", "user_id", "status"),
        Index("ix_tasks_user_scheduled", "user_id", "scheduled_start"),
    )


class Protocol(Base):
    __tablename__ = "protocols"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[ProtocolType] = mapped_column(Enum(ProtocolType), index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    definition: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="protocols")
    tasks: Mapped[list["Task"]] = relationship(back_populates="protocol", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_protocols_user_type", "user_id", "type"),
        Index("ix_protocols_user_active", "user_id", "is_active"),
    )


class CommsTemplate(Base):
    __tablename__ = "comms_templates"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient_type: Mapped[str] = mapped_column(String(64), nullable=False)

    subject_template: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    body_template: Mapped[str] = mapped_column(Text, nullable=False)

    ai_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="comms_templates")


class AdminItem(Base):
    __tablename__ = "admin_items"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    frequency: Mapped[str] = mapped_column(String(32), nullable=False)
    day_of_month: Mapped[Optional[int]] = mapped_column(nullable=True)
    day_of_week: Mapped[Optional[int]] = mapped_column(nullable=True)
    month: Mapped[Optional[int]] = mapped_column(nullable=True)

    next_due: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    last_completed: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    draft_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ai_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="admin_items")

    __table_args__ = (
        Index("ix_admin_items_user_due", "user_id", "next_due"),
    )


class ProtocolRun(Base):
    __tablename__ = "protocol_runs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False)
    protocol_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("protocols.id"), index=True, nullable=False)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)

    tasks_created: Mapped[int] = mapped_column(default=0, nullable=False)
    tasks_completed: Mapped[int] = mapped_column(default=0, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship()
    protocol: Mapped["Protocol"] = relationship()
    tasks: Mapped[list["Task"]] = relationship(back_populates="protocol_run")
    steps: Mapped[list["ProtocolStepRun"]] = relationship(
        back_populates="protocol_run",
        cascade="all, delete-orphan",
        order_by="ProtocolStepRun.step_index",
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "protocol_id",
            "idempotency_key",
            name="uq_protocol_runs_user_protocol_idempotency",
        ),
    )


class ProtocolStepRun(Base):
    __tablename__ = "protocol_step_runs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    protocol_run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("protocol_runs.id"), index=True, nullable=False
    )
    step_index: Mapped[int] = mapped_column(nullable=False)
    step_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    provider: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(nullable=True)
    tool_activity: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    output_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    protocol_run: Mapped["ProtocolRun"] = relationship(back_populates="steps")

    __table_args__ = (
        UniqueConstraint(
            "protocol_run_id",
            "step_index",
            name="uq_protocol_step_runs_run_index",
        ),
    )
