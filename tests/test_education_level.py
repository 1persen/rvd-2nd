"""Kategori pendidikan hanya wajib dan divalidasi pada admin team create/update.

Alur publik (voting, checkout, settlement) tidak boleh gagal karena tim belum
mempunyai kategori pendidikan.
"""
import uuid

import pytest
import pytest_asyncio

from app.seeds.admin import seed_admin


@pytest_asyncio.fixture
async def admin_headers(async_client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.local"
    password = uuid.uuid4().hex
    await seed_admin(email, password)
    login = await async_client.post(
        "/api/v1/auth/admin/login", json={"email": email, "password": password}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


@pytest.mark.asyncio
async def test_admin_team_create_requires_education_level(async_client, admin_headers, test_data):
    response = await async_client.post(
        f"/api/v1/admin/events/{test_data['event'].id}/teams",
        json={"name": "Tim Tanpa Kategori", "school": "Sekolah"},
        headers=admin_headers,
    )
    assert response.status_code == 400
    assert response.json()["message"] == "Kategori pendidikan wajib dipilih"


@pytest.mark.asyncio
async def test_admin_team_create_rejects_unknown_level(async_client, admin_headers, test_data):
    response = await async_client.post(
        f"/api/v1/admin/events/{test_data['event'].id}/teams",
        json={"name": "Tim Kategori Salah", "school": "Sekolah", "education_level": "sma"},
        headers=admin_headers,
    )
    assert response.status_code == 400
    assert response.json()["message"] == "Kategori pendidikan tidak valid"


@pytest.mark.asyncio
async def test_admin_team_create_accepts_allowed_levels(async_client, admin_headers, test_data):
    for level in ("smp-mts", "sma-smk"):
        response = await async_client.post(
            f"/api/v1/admin/events/{test_data['event'].id}/teams",
            json={"name": f"Tim {level}", "school": "Sekolah", "education_level": level},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["education_level"] == level


@pytest.mark.asyncio
async def test_admin_team_update_validates_only_a_supplied_level(async_client, admin_headers, test_data):
    event_id = test_data["event"].id
    team_id = test_data["team"].id
    # Level may be omitted on update; the team keeps its current value.
    response = await async_client.put(
        f"/api/v1/admin/events/{event_id}/teams/{team_id}",
        json={"name": "Tim Diperbarui"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    invalid = await async_client.put(
        f"/api/v1/admin/events/{event_id}/teams/{team_id}",
        json={"education_level": "unknown"},
        headers=admin_headers,
    )
    assert invalid.status_code == 400
    assert invalid.json()["message"] == "Kategori pendidikan tidak valid"


@pytest.mark.asyncio
async def test_public_vote_page_does_not_require_education_level(async_client, test_data):
    # The shared fixture team has no category; the public flow must still work.
    assert test_data["team"].education_level is None
    response = await async_client.get(
        f"/events/{test_data['event'].id}/vote/{test_data['team'].id}"
    )
    assert response.status_code == 200
