import uuid

import pytest

from app.seeds.admin import seed_admin


@pytest.mark.asyncio
async def test_admin_event_requires_static_category(async_client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.local"
    password = uuid.uuid4().hex
    await seed_admin(email, password)
    login = await async_client.post(
        "/api/v1/auth/admin/login", json={"email": email, "password": password}
    )
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    payload = {"name": f"Festival {uuid.uuid4().hex[:8]}", "category": "invalid"}
    invalid = await async_client.post("/api/v1/admin/events", json=payload, headers=headers)
    assert invalid.status_code == 422

    payload["category"] = "smp-mts"
    created = await async_client.post("/api/v1/admin/events", json=payload, headers=headers)
    assert created.status_code == 200, created.text
    event = created.json()["data"]
    assert event["category"] == "smp-mts"
    assert event["slug"].startswith("festival-")

    edited = await async_client.put(
        f"/api/v1/admin/events/{event['id']}",
        json={"name": "Nama baru"},
        headers=headers,
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["slug"] == event["slug"]
