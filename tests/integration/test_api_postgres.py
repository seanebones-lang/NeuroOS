"""Critical API contracts exercised against PostgreSQL."""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import neuro_os.api as api_module
from neuro_os.models import ProtocolRun, Task

pytestmark = pytest.mark.integration
EXPECTED_MORNING_TASKS = 3


async def _register_and_login(
    client: httpx.AsyncClient,
    email: str,
) -> dict[str, str]:
    credentials = {
        "email": email,
        "password": "integration-password-123",
        "full_name": "Integration User",
        "timezone": "America/Chicago",
    }
    register_response = await client.post("/auth/register", json=credentials)
    assert register_response.status_code == HTTPStatus.CREATED, register_response.text
    login_response = await client.post(
        "/auth/login",
        json={"email": credentials["email"], "password": credentials["password"]},
    )
    assert login_response.status_code == HTTPStatus.OK, login_response.text
    return {"Authorization": f"Bearer {login_response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_auth_ownership_lifecycle_and_rollback(api_client: httpx.AsyncClient) -> None:
    assert (await api_client.get("/tasks")).status_code == HTTPStatus.UNAUTHORIZED
    owner_headers = await _register_and_login(api_client, "owner@example.com")
    other_headers = await _register_and_login(api_client, "other@example.com")

    assert (await api_client.get("/energy/check-in", headers=owner_headers)).json() is None
    saved_energy = await api_client.put(
        "/energy/check-in", headers=owner_headers, json={"energy_level": "recovery"}
    )
    assert saved_energy.status_code == HTTPStatus.OK, saved_energy.text
    assert saved_energy.json()["energy_level"] == "recovery"
    updated_energy = await api_client.put(
        "/energy/check-in", headers=owner_headers, json={"energy_level": "deep"}
    )
    assert updated_energy.json()["energy_level"] == "deep"
    assert (await api_client.get("/energy/check-in", headers=other_headers)).json() is None

    parent_response = await api_client.post(
        "/tasks",
        headers=owner_headers,
        json={"title": "Parent task", "energy_level": "deep"},
    )
    assert parent_response.status_code == HTTPStatus.CREATED, parent_response.text
    parent = parent_response.json()

    child_response = await api_client.post(
        "/tasks",
        headers=owner_headers,
        json={"title": "Child task", "parent_id": parent["id"]},
    )
    assert child_response.status_code == HTTPStatus.CREATED, child_response.text

    assert (
        await api_client.get(f"/tasks/{parent['id']}", headers=other_headers)
    ).status_code == HTTPStatus.NOT_FOUND
    assert (
        await api_client.patch(
            f"/tasks/{parent['id']}",
            headers=other_headers,
            json={"title": "Unauthorized change"},
        )
    ).status_code == HTTPStatus.NOT_FOUND

    blocked_delete = await api_client.delete(f"/tasks/{parent['id']}", headers=owner_headers)
    assert blocked_delete.status_code == HTTPStatus.CONFLICT
    assert (
        await api_client.get(f"/tasks/{parent['id']}", headers=owner_headers)
    ).status_code == HTTPStatus.OK

    invalid_schedule = await api_client.patch(
        f"/tasks/{parent['id']}",
        headers=owner_headers,
        json={"scheduled_end": "2026-09-15T10:00:00-05:00"},
    )
    assert invalid_schedule.status_code == HTTPStatus.BAD_REQUEST
    unchanged = await api_client.get(f"/tasks/{parent['id']}", headers=owner_headers)
    assert unchanged.json()["scheduled_end"] is None

    completed = await api_client.patch(
        f"/tasks/{parent['id']}", headers=owner_headers, json={"status": "done"}
    )
    assert completed.status_code == HTTPStatus.OK
    invalid_transition = await api_client.patch(
        f"/tasks/{parent['id']}", headers=owner_headers, json={"status": "in_progress"}
    )
    assert invalid_transition.status_code == HTTPStatus.CONFLICT
    still_completed = await api_client.get(f"/tasks/{parent['id']}", headers=owner_headers)
    assert still_completed.json()["status"] == "done"


class DeterministicMorningAgent:
    provider = "test"
    model = "deterministic-morning"

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def run(self, prompt: str, _context) -> str:
        await asyncio.sleep(0.02)
        if "Step: gather_inputs" in prompt:
            return '{"open_tasks":[],"calendar_events":[]}'
        if "Step: assess_energy" in prompt:
            return '{"energy_blocks":[]}'
        if "Step: sequence_blocks" in prompt:
            return json.dumps(
                {
                    "blocks": [
                        {"title": "Deep work", "energy_level": "deep", "estimated_minutes": 45},
                        {
                            "title": "Messages",
                            "energy_level": "shallow",
                            "estimated_minutes": 20,
                        },
                        {
                            "title": "Reset",
                            "energy_level": "recovery",
                            "estimated_minutes": 15,
                        },
                    ]
                }
            )
        return "Plan ready"


@pytest.mark.asyncio
async def test_concurrent_morning_requests_converge_on_one_run(
    api_client: httpx.AsyncClient,
    integration_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_module, "Agent", DeterministicMorningAgent)
    headers = await _register_and_login(api_client, "concurrency@example.com")
    headers["Idempotency-Key"] = "integration-concurrent-morning"

    first, second = await asyncio.gather(
        api_client.post("/morning/plan", headers=headers),
        api_client.post("/morning/plan", headers=headers),
    )

    expected_statuses = (
        [HTTPStatus.OK, HTTPStatus.OK],
        [HTTPStatus.OK, HTTPStatus.CONFLICT],
    )
    assert sorted([first.status_code, second.status_code]) in expected_statuses
    successful = first if first.status_code == HTTPStatus.OK else second
    assert successful.json()["tasks_created"] == EXPECTED_MORNING_TASKS
    history = await api_client.get("/protocols/runs", headers=headers)
    assert history.status_code == HTTPStatus.OK, history.text
    assert len(history.json()) == 1
    saved_run = history.json()[0]
    assert saved_run["run_id"] == successful.json()["run_id"]
    assert saved_run["status"] == "completed"
    assert saved_run["idempotency_key"] == "integration-concurrent-morning"
    assert saved_run["tasks_created"] == EXPECTED_MORNING_TASKS
    other_headers = await _register_and_login(api_client, "run-history-other@example.com")
    assert (await api_client.get("/protocols/runs", headers=other_headers)).json() == []
    async with integration_session_factory() as session:
        run_count = await session.scalar(select(func.count()).select_from(ProtocolRun))
        task_count = await session.scalar(select(func.count()).select_from(Task))
    assert run_count == 1
    assert task_count == EXPECTED_MORNING_TASKS
