"""Browser workspace delivery tests."""

from http import HTTPStatus

import httpx
import pytest

from neuro_os.api import app


@pytest.mark.asyncio
async def test_browser_workspace_is_served_from_the_api_origin():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == HTTPStatus.OK
    assert "Make the next thing" in response.text
    assert "Open the focused workspace preview" in response.text
    assert "Leave a handoff" in response.text
    assert "Return without rebuilding" in response.text
    assert "Protocol trail" in response.text
    assert "Plan trace" in response.text
    assert "Today is set to" in response.text
