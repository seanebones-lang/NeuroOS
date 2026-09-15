"""FastAPI application for NeuroOS."""

from __future__ import annotations
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from neuro_os.config import settings
from neuro_os.database import init_db, close_db, get_session, engine
from neuro_os.models import User, Task, Protocol, ProtocolType, TaskStatus, EnergyLevel, AdminItem, CommsTemplate
from neuro_os.memory import MemoryManager
from neuro_os.agent import Agent, AgentContext
from neuro_os.protocols import ProtocolEngine, DEFAULT_PROTOCOLS
from neuro_os.scheduler import EnergyAwareScheduler, create_default_energy_profile


# Security
security = HTTPBearer(auto_error=False)


# Pydantic schemas
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    timezone: str = "America/Chicago"


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: Optional[str]
    timezone: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    energy_level: EnergyLevel = EnergyLevel.SHALLOW
    estimated_minutes: Optional[int] = None
    parent_id: Optional[UUID] = None
    protocol_id: Optional[UUID] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[TaskStatus] = None
    energy_level: Optional[EnergyLevel] = None
    estimated_minutes: Optional[int] = None
    scheduled_start: Optional[datetime] = None
    scheduled_end: Optional[datetime] = None


class TaskResponse(BaseModel):
    id: UUID
    user_id: UUID
    parent_id: Optional[UUID]
    protocol_id: Optional[UUID]
    title: str
    description: Optional[str]
    status: TaskStatus
    energy_level: EnergyLevel
    sequence: int
    estimated_minutes: Optional[int]
    actual_minutes: Optional[int]
    scheduled_start: Optional[datetime]
    scheduled_end: Optional[datetime]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProtocolRunRequest(BaseModel):
    protocol_type: ProtocolType
    initial_context: Optional[dict] = None


class AdminItemCreate(BaseModel):
    name: str
    category: str
    description: Optional[str] = None
    frequency: str
    day_of_month: Optional[int] = None
    day_of_week: Optional[int] = None
    month: Optional[int] = None
    next_due: datetime
    draft_template: Optional[str] = None
    ai_instructions: Optional[str] = None


class CommsTemplateCreate(BaseModel):
    name: str
    channel: str
    recipient_type: str
    subject_template: Optional[str] = None
    body_template: str
    ai_instructions: Optional[str] = None


class MorningPlanResponse(BaseModel):
    blocks: list[dict]
    tasks_created: int


# Auth utilities
def create_access_token(user_id: UUID) -> str:
    payload = {"sub": str(user_id), "exp": datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def verify_token(token: str) -> Optional[UUID]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        return UUID(payload.get("sub"))
    except JWTError:
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    session: AsyncSession = Depends(get_session),
) -> User:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user_id = verify_token(credentials.credentials)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


# Redis client (lazy init)
_redis_client = None


async def get_redis():
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as redis
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


# Memory manager dependency
async def get_memory_manager(
    session: AsyncSession = Depends(get_session),
) -> MemoryManager:
    redis_client = await get_redis()
    return MemoryManager(session, redis_client)


# Lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()


# App
app = FastAPI(
    title="NeuroOS API",
    description="External executive function for neurodivergent builders",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Auth endpoints
@app.post("/auth/register", response_model=UserResponse, status_code=201)
async def register(user_data: UserCreate, session: AsyncSession = Depends(get_session)):
    # Check existing
    result = await session.execute(select(User).where(User.email == user_data.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    # Create user
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    user = User(
        email=user_data.email,
        hashed_password=pwd_context.hash(user_data.password),
        full_name=user_data.full_name,
        timezone=user_data.timezone,
    )
    session.add(user)
    await session.flush()

    # Create default energy profile
    from neuro_os.models import EnergyProfile
    energy_profile = EnergyProfile(
        user_id=user.id,
        weekly_pattern=create_default_energy_profile(user.timezone),
    )
    session.add(energy_profile)

    # Create default protocols
    for ptype, pdef in DEFAULT_PROTOCOLS.items():
        protocol = Protocol(
            user_id=user.id,
            name=pdef.name,
            type=ptype,
            description=pdef.description,
            definition={
                "steps": [
                    {
                        "name": s.name,
                        "description": s.description,
                        "agent_prompt": s.agent_prompt,
                        "tool_names": s.tool_names,
                        "output_key": s.output_key,
                    }
                    for s in pdef.steps
                ]
            },
            is_default=True,
        )
        session.add(protocol)

    await session.commit()
    await session.refresh(user)
    return user


@app.post("/auth/login", response_model=Token)
async def login(credentials: UserLogin, session: AsyncSession = Depends(get_session)):
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    result = await session.execute(select(User).where(User.email == credentials.email))
    user = result.scalar_one_or_none()
    if not user or not pwd_context.verify(credentials.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(user.id)
    return {"access_token": token, "token_type": "bearer"}


@app.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


# Task endpoints
@app.post("/tasks", response_model=TaskResponse, status_code=201)
async def create_task(
    task_data: TaskCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    task = Task(
        user_id=current_user.id,
        **task_data.model_dump(),
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


@app.get("/tasks", response_model=list[TaskResponse])
async def list_tasks(
    status: Optional[TaskStatus] = None,
    energy_level: Optional[EnergyLevel] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    query = select(Task).where(Task.user_id == current_user.id)
    if status:
        query = query.where(Task.status == status)
    if energy_level:
        query = query.where(Task.energy_level == energy_level)
    query = query.order_by(Task.sequence, Task.scheduled_start.nulls_last()).limit(limit).offset(offset)
    result = await session.execute(query)
    return result.scalars().all()


@app.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Task).where(Task.id == task_id, Task.user_id == current_user.id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: UUID,
    task_data: TaskUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Task).where(Task.id == task_id, Task.user_id == current_user.id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    for field, value in task_data.model_dump(exclude_unset=True).items():
        setattr(task, field, value)

    if task_data.status == TaskStatus.IN_PROGRESS and not task.started_at:
        task.started_at = datetime.utcnow()
    elif task_data.status == TaskStatus.DONE and not task.completed_at:
        task.completed_at = datetime.utcnow()

    await session.commit()
    await session.refresh(task)
    return task


@app.delete("/tasks/{task_id}", status_code=204)
async def delete_task(
    task_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Task).where(Task.id == task_id, Task.user_id == current_user.id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await session.delete(task)
    await session.commit()


# Protocol endpoints
@app.post("/protocols/run", response_model=dict)
async def run_protocol(
    request: ProtocolRunRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    memory: MemoryManager = Depends(get_memory_manager),
):
    # Create a simple agent factory for protocol types
    def agent_factory(ptype: ProtocolType) -> Agent:
        prompts = {
            ProtocolType.MORNING: "You are the Morning Protocol agent. Output JSON with blocks[title, energy_level, estimated_minutes, task_ids].",
            ProtocolType.INTERRUPTION_RECOVERY: "You are the Interruption Recovery agent. Output one sentence: exact next micro-step.",
            ProtocolType.SHUTDOWN: "You are the Shutdown Protocol agent. Output JSON with tomorrow_items[title, energy_level, reason] + personal_note.",
            ProtocolType.WEEKLY_REVIEW: "You are the Weekly Review agent. Output energy adjustments + admin batches.",
            ProtocolType.ADMIN_BATCH: "You are the Admin Batch agent. Output drafts for each admin item.",
            ProtocolType.COMMS_DRAFT: "You are the Comms Drafter. Output draft message matching user's voice.",
        }
        return Agent(
            tools=[],
            system_prompt=prompts.get(ptype, "You are a helpful assistant."),
        )

    engine = ProtocolEngine(session, memory, agent_factory)
    run = await engine.run_protocol(request.protocol_type, current_user.id, request.initial_context)

    return {
        "run_id": str(run.id),
        "protocol_type": request.protocol_type.value,
        "status": run.status,
        "tasks_created": run.tasks_created,
        "tasks_completed": run.tasks_completed,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }


@app.get("/protocols", response_model=list[dict])
async def list_protocols(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Protocol).where(Protocol.user_id == current_user.id, Protocol.is_active == True)
    )
    protocols = result.scalars().all()
    return [
        {
            "id": str(p.id),
            "name": p.name,
            "type": p.type.value,
            "description": p.description,
            "is_default": p.is_default,
        }
        for p in protocols
    ]


# Morning plan endpoint (specialized)
@app.post("/morning/plan", response_model=MorningPlanResponse)
async def generate_morning_plan(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    memory: MemoryManager = Depends(get_memory_manager),
):
    def agent_factory(ptype: ProtocolType) -> Agent:
        return Agent(
            tools=[],
            system_prompt="You are the Morning Protocol agent. Output JSON with blocks[title, energy_level, estimated_minutes, task_ids].",
        )

    engine = ProtocolEngine(session, memory, agent_factory)
    run = await engine.run_protocol(ProtocolType.MORNING, current_user.id)

    # Get created tasks
    result = await session.execute(
        select(Task).where(Task.protocol_id == run.protocol_id, Task.user_id == current_user.id)
    )
    tasks = result.scalars().all()

    blocks = [
        {
            "title": t.title,
            "energy_level": t.energy_level.value,
            "estimated_minutes": t.estimated_minutes,
            "task_id": str(t.id),
        }
        for t in tasks
    ]

    return {"blocks": blocks, "tasks_created": run.tasks_created}


# Admin endpoints
@app.post("/admin", response_model=dict, status_code=201)
async def create_admin_item(
    item_data: AdminItemCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    item = AdminItem(user_id=current_user.id, **item_data.model_dump())
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return {"id": str(item.id), "name": item.name, "next_due": item.next_due}


@app.get("/admin", response_model=list[dict])
async def list_admin_items(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(AdminItem).where(AdminItem.user_id == current_user.id, AdminItem.is_active == True)
        .order_by(AdminItem.next_due)
    )
    items = result.scalars().all()
    return [
        {
            "id": str(i.id),
            "name": i.name,
            "category": i.category,
            "frequency": i.frequency,
            "next_due": i.next_due,
            "last_completed": i.last_completed,
        }
        for i in items
    ]


# Comms endpoints
@app.post("/comms/templates", response_model=dict, status_code=201)
async def create_comms_template(
    template_data: CommsTemplateCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    template = CommsTemplate(user_id=current_user.id, **template_data.model_dump())
    session.add(template)
    await session.commit()
    await session.refresh(template)
    return {"id": str(template.id), "name": template.name}


@app.get("/comms/templates", response_model=list[dict])
async def list_comms_templates(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(CommsTemplate).where(CommsTemplate.user_id == current_user.id)
    )
    templates = result.scalars().all()
    return [
        {
            "id": str(t.id),
            "name": t.name,
            "channel": t.channel,
            "recipient_type": t.recipient_type,
        }
        for t in templates
    ]


# Health
@app.get("/health")
async def health():
    return {"status": "ok", "service": "neuro-os", "version": "0.1.0"}


# Need timedelta import
from datetime import timedelta
