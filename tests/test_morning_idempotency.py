"""Default morning-plan idempotency behavior."""

from datetime import UTC, datetime, timedelta

import pytest

from neuro_os.api import morning_plan_idempotency_key
from neuro_os.models import DailyEnergyCheckIn, EnergyLevel, User


@pytest.mark.asyncio
async def test_changed_capacity_creates_a_new_default_morning_key(session) -> None:
    user = User(
        email="capacity-key@example.com",
        hashed_password="test",
        timezone="America/Chicago",
    )
    session.add(user)
    await session.flush()
    check_in = DailyEnergyCheckIn(
        user_id=user.id,
        check_in_date=datetime.now().date(),
        energy_level=EnergyLevel.RECOVERY,
        updated_at=datetime(2026, 9, 16, 8, tzinfo=UTC),
    )
    session.add(check_in)
    await session.commit()

    recovery_key = await morning_plan_idempotency_key(session, user)
    check_in.energy_level = EnergyLevel.DEEP
    check_in.updated_at = check_in.updated_at + timedelta(minutes=1)
    await session.commit()
    deep_key = await morning_plan_idempotency_key(session, user)

    assert recovery_key != deep_key
    assert ":capacity:recovery:" in recovery_key
    assert ":capacity:deep:" in deep_key
