"""CLI for NeuroOS."""

from __future__ import annotations
import asyncio
from datetime import datetime
from typing import Optional
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

from neuro_os.config import settings
from neuro_os.database import init_db, get_session, AsyncSessionLocal
from neuro_os.models import User, Task, TaskStatus, EnergyLevel, ProtocolType
from neuro_os.agent import Agent, AgentContext
from neuro_os.memory import MemoryManager
from neuro_os.protocols import ProtocolEngine, DEFAULT_PROTOCOLS
from neuro_os.scheduler import EnergyAwareScheduler, create_default_energy_profile

app = typer.Typer(name="neuro-os", help="External executive function for neurodivergent builders")
console = Console()


# Auth helpers
def get_pwd_context():
    from passlib.context import CryptContext
    return CryptContext(schemes=["bcrypt"], deprecated="auto")


async def get_user_by_email(email: str) -> Optional[User]:
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        result = await session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()


async def create_user(email: str, password: str, full_name: Optional[str] = None, timezone: str = "America/Chicago") -> User:
    async with AsyncSessionLocal() as session:
        user = User(
            email=email,
            hashed_password=get_pwd_context().hash(password),
            full_name=full_name,
            timezone=timezone,
        )
        session.add(user)
        await session.flush()

        from neuro_os.models import EnergyProfile, Protocol
        energy_profile = EnergyProfile(
            user_id=user.id,
            weekly_pattern=create_default_energy_profile(timezone),
        )
        session.add(energy_profile)

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


# Commands
@app.command()
def init_db_cmd():
    """Initialize database tables."""
    asyncio.run(init_db())
    console.print("[green]Database initialized[/green]")


@app.command()
def register(
    email: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True),
    full_name: Optional[str] = typer.Option(None, prompt=True),
    timezone: str = typer.Option("America/Chicago", prompt=True),
):
    """Register a new user."""
    async def _register():
        existing = await get_user_by_email(email)
        if existing:
            console.print("[red]Email already registered[/red]")
            raise typer.Exit(1)
        user = await create_user(email, password, full_name, timezone)
        console.print(f"[green]Created user: {user.email} (ID: {user.id})[/green]")
    asyncio.run(_register())


@app.command()
def morning(
    email: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True),
):
    """Run morning protocol - plan the day."""
    async def _morning():
        user = await get_user_by_email(email)
        if not user or not get_pwd_context().verify(password, user.hashed_password):
            console.print("[red]Invalid credentials[/red]")
            raise typer.Exit(1)

        async with AsyncSessionLocal() as session:
            from neuro_os.memory import MemoryManager
            import redis.asyncio as redis
            redis_client = redis.from_url(settings.redis_url, decode_responses=True)
            memory = MemoryManager(session, redis_client)

            def agent_factory(ptype):
                prompts = {
                    ProtocolType.MORNING: "You are the Morning Protocol agent. Output JSON with blocks[title, energy_level, estimated_minutes, task_ids].",
                }
                return Agent(tools=[], system_prompt=prompts.get(ptype, ""))

            engine = ProtocolEngine(session, memory, agent_factory)
            run = await engine.run_protocol(ProtocolType.MORNING, user.id)

            # Display results
            from sqlalchemy import select
            from neuro_os.models import Task
            result = await session.execute(
                select(Task).where(Task.protocol_id == run.protocol_id, Task.user_id == user.id)
            )
            tasks = result.scalars().all()

            console.print(Panel(f"[bold]Morning Plan - {datetime.now().strftime('%A, %B %d')}[/bold]", style="blue"))
            table = Table(show_header=True, header_style="bold")
            table.add_column("Block")
            table.add_column("Title")
            table.add_column("Energy")
            table.add_column("Est. Min")
            for i, t in enumerate(tasks, 1):
                table.add_row(str(i), t.title, t.energy_level.value, str(t.estimated_minutes or "?"))
            console.print(table)
            console.print(f"\n[green]Created {len(tasks)} tasks[/green]")

    asyncio.run(_morning())


@app.command()
def recover(
    email: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True),
    task_id: Optional[str] = typer.Option(None, help="Task ID to recover (defaults to most recent in_progress)"),
):
    """Run interruption recovery protocol."""
    async def _recover():
        user = await get_user_by_email(email)
        if not user or not get_pwd_context().verify(password, user.hashed_password):
            console.print("[red]Invalid credentials[/red]")
            raise typer.Exit(1)

        async with AsyncSessionLocal() as session:
            from neuro_os.memory import MemoryManager
            import redis.asyncio as redis
            redis_client = redis.from_url(settings.redis_url, decode_responses=True)
            memory = MemoryManager(session, redis_client)

            # Find interrupted task
            from sqlalchemy import select
            from neuro_os.models import Task
            if task_id:
                result = await session.execute(select(Task).where(Task.id == UUID(task_id), Task.user_id == user.id))
            else:
                result = await session.execute(
                    select(Task).where(Task.user_id == user.id, Task.status == TaskStatus.IN_PROGRESS)
                    .order_by(Task.started_at.desc()).limit(1)
                )
            task = result.scalar_one_or_none()
            if not task:
                console.print("[yellow]No interrupted task found[/yellow]")
                return

            context = {"interrupted_task_id": str(task.id)}
            def agent_factory(ptype):
                return Agent(tools=[], system_prompt="You are the Interruption Recovery agent. Output one sentence: exact next micro-step.")

            engine = ProtocolEngine(session, memory, agent_factory)
            run = await engine.run_protocol(ProtocolType.INTERRUPTION_RECOVERY, user.id, context)

            console.print(Panel(f"[bold]Recovery for: {task.title}[/bold]", style="yellow"))
            console.print(f"[green]Resume step:[/green] {run.notes or 'Check working memory'}")

    asyncio.run(_recover())


@app.command()
def shutdown(
    email: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True),
):
    """Run shutdown protocol - end of day wrap up."""
    async def _shutdown():
        user = await get_user_by_email(email)
        if not user or not get_pwd_context().verify(password, user.hashed_password):
            console.print("[red]Invalid credentials[/red]")
            raise typer.Exit(1)

        async with AsyncSessionLocal() as session:
            from neuro_os.memory import MemoryManager
            import redis.asyncio as redis
            redis_client = redis.from_url(settings.redis_url, decode_responses=True)
            memory = MemoryManager(session, redis_client)

            def agent_factory(ptype):
                return Agent(tools=[], system_prompt="You are the Shutdown Protocol agent. Output JSON with tomorrow_items + personal_note.")

            engine = ProtocolEngine(session, memory, agent_factory)
            run = await engine.run_protocol(ProtocolType.SHUTDOWN, user.id)

            console.print(Panel("[bold]Shutdown Complete[/bold]", style="green"))
            console.print(f"Tasks created for tomorrow: {run.tasks_created}")
            if run.notes:
                console.print(f"Notes: {run.notes}")

    asyncio.run(_shutdown())


@app.command()
def admin(
    email: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True),
):
    """Run admin batch - process due admin items."""
    async def _admin():
        user = await get_user_by_email(email)
        if not user or not get_pwd_context().verify(password, user.hashed_password):
            console.print("[red]Invalid credentials[/red]")
            raise typer.Exit(1)

        async with AsyncSessionLocal() as session:
            from neuro_os.memory import MemoryManager
            import redis.asyncio as redis
            redis_client = redis.from_url(settings.redis_url, decode_responses=True)
            memory = MemoryManager(session, redis_client)

            def agent_factory(ptype):
                return Agent(tools=[], system_prompt="You are the Admin Batch agent. Output drafts for each admin item.")

            engine = ProtocolEngine(session, memory, agent_factory)
            run = await engine.run_protocol(ProtocolType.ADMIN_BATCH, user.id)

            console.print(Panel("[bold]Admin Batch Complete[/bold]", style="cyan"))
            console.print(f"Items processed: {run.tasks_created}")

    asyncio.run(_admin())


@app.command()
def tasks(
    email: str = typer.Option(..., prompt=True),
    password: str = typer.Option(..., prompt=True, hide_input=True),
    status_filter: Optional[str] = typer.Option(None, "--status", "-s"),
    energy: Optional[str] = typer.Option(None, "--energy", "-e"),
):
    """List tasks."""
    async def _tasks():
        user = await get_user_by_email(email)
        if not user or not get_pwd_context().verify(password, user.hashed_password):
            console.print("[red]Invalid credentials[/red]")
            raise typer.Exit(1)

        async with AsyncSessionLocal() as session:
            from sqlalchemy import select
            from neuro_os.models import Task
            query = select(Task).where(Task.user_id == user.id)
            if status_filter:
                query = query.where(Task.status == TaskStatus(status_filter))
            if energy:
                query = query.where(Task.energy_level == EnergyLevel(energy))
            query = query.order_by(Task.sequence, Task.scheduled_start.nulls_last()).limit(50)
            result = await session.execute(query)
            tasks = result.scalars().all()

            table = Table(show_header=True, header_style="bold")
            table.add_column("ID", style="dim")
            table.add_column("Title")
            table.add_column("Status")
            table.add_column("Energy")
            table.add_column("Est. Min")
            table.add_column("Scheduled")
            for t in tasks:
                scheduled = t.scheduled_start.strftime("%m/%d %H:%M") if t.scheduled_start else "—"
                table.add_row(str(t.id)[:8], t.title, t.status.value, t.energy_level.value, str(t.estimated_minutes or "—"), scheduled)
            console.print(table)

    asyncio.run(_tasks())


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
):
    """Run the FastAPI server."""
    import uvicorn
    uvicorn.run("neuro_os.api:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
