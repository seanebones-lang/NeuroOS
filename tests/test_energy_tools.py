"""Energy tool tests for current-day capacity context."""

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from neuro_os import database
from neuro_os.agent import AgentContext
from neuro_os.models import DailyEnergyCheckIn, EnergyLevel, User
from neuro_os.tools import GetEnergyProfileTool


@pytest.mark.asyncio
async def test_energy_profile_includes_the_users_stated_capacity(
    engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "AsyncSessionLocal", session_factory)

    async with session_factory() as session:
        user = User(
            email="energy-tool@example.com",
            hashed_password="test",
            timezone="America/Chicago",
        )
        session.add(user)
        await session.flush()
        session.add(
            DailyEnergyCheckIn(
                user_id=user.id,
                check_in_date=datetime.now(ZoneInfo(user.timezone)).date(),
                energy_level=EnergyLevel.RECOVERY,
            )
        )
        await session.commit()
        user_id = user.id

    result = await GetEnergyProfileTool().execute(
        {}, AgentContext(user_id=user_id, session_id=uuid4())
    )

    assert result.error is None
    assert result.result["stated_capacity"] == "recovery"
    assert result.result["today_schedule"]
