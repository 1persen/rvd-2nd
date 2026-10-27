"""Integration tests for the team Excel import endpoints (preview/confirm)."""
import hashlib
import io
import uuid

import openpyxl
import pytest

from app.enums.education_level import EDUCATION_LABELS
from app.enums.event_status import EventStatus
from app.models.team import Team
from app.schemas.event import EventCreate
from app.schemas.team import TeamCreate
from app.seeds.admin import seed_admin
from app.services import team_import_service as svc
from app.services.event_service import create_event, create_team
from sqlalchemy import select, func


async def _event_with_category(db_session, category="sd-mi"):
    return await create_event(db_session, EventCreate(
        name=f"Event {uuid.uuid4().hex[:8]}",
        category=category,
        status=EventStatus.VOTING_OPEN,
        price_per_vote=1000,
    ))


def _xlsx_bytes(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(svc.IMPORT_COLUMNS))
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _admin_headers(async_client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.local"
    password = uuid.uuid4().hex
    await seed_admin(email, password)
    login = await async_client.post(
        "/api/v1/auth/admin/login", json={"email": email, "password": password}
    )
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


async def _event_with_category(db_session, category="sd-mi"):
    return await create_event(db_session, EventCreate(
        name=f"Event {uuid.uuid4().hex[:8]}",
        category=category,
        status=EventStatus.VOTING_OPEN,
        price_per_vote=1000,
    ))


async def _count_teams(db_session, event_id):
    result = await db_session.execute(
        select(func.count(Team.id)).where(Team.event_id == event_id)
    )
    return int(result.scalar_one())


@pytest.mark.asyncio
async def test_template_download(async_client, db_session):
    headers = await _admin_headers(async_client)
    event = await _event_with_category(db_session)
    await db_session.commit()
    res = await async_client.get(
        f"/api/v1/admin/events/{event.id}/teams/import/template", headers=headers
    )
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-store"
    wb = openpyxl.load_workbook(io.BytesIO(res.content))
    assert list(next(wb.active.iter_rows(values_only=True))) == list(svc.IMPORT_COLUMNS)


@pytest.mark.asyncio
async def test_preview_reports_and_does_not_write(async_client, db_session):
    headers = await _admin_headers(async_client)
    event = await _event_with_category(db_session)
    await create_team(db_session, TeamCreate(
        event_id=event.id, name="Tim Ada", school="SMA Ada", education_level="sd-mi",
    ))
    await db_session.commit()
    before = await _count_teams(db_session, event.id)

    content = _xlsx_bytes([
        ["Tim Baru", "SMA Baru", "SD/MI", None, 1],
        ["Tim Ada", "SMA Ada", "SD/MI", None, 2],   # duplicate of existing team
        ["Tim Dua", "SMA Dua", "SD/MI", None, 3],
        ["Tim Dua", "SMA Dua", "SD/MI", None, 4],   # duplicate inside file
        ["Tim Invalid", None, "Kuliah", None, 5],   # unknown category
    ])
    res = await async_client.post(
        f"/api/v1/admin/events/{event.id}/teams/import/preview",
        files={"file": ("peserta.xlsx", content, svc.XLSX_MIME)},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    report = res.json()["data"]
    assert report["total_rows"] == 5
    assert report["valid_count"] == 2
    assert report["duplicate_existing_count"] == 1
    assert report["duplicate_in_file_count"] == 1
    assert report["invalid_count"] == 1
    assert report["can_confirm"] is False
    assert report["sha256"] == hashlib.sha256(content).hexdigest()
    assert await _count_teams(db_session, event.id) == before


@pytest.mark.asyncio
async def test_confirm_happy_path_and_audit(async_client, db_session):
    headers = await _admin_headers(async_client)
    event = await _event_with_category(db_session)
    await db_session.commit()

    content = _xlsx_bytes([
        ["Tim Satu", "SMA Satu", "SD/MI", "deskripsi", 3],
        ["Tim Dua", None, "sd-mi", None, None],
    ])
    sha = hashlib.sha256(content).hexdigest()
    res = await async_client.post(
        f"/api/v1/admin/events/{event.id}/teams/import/confirm",
        files={"file": ("peserta.xlsx", content, svc.XLSX_MIME)},
        data={"sha256": sha},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    payload = res.json()["data"]
    assert payload["imported_count"] == 2
    assert {row["nama_tim"] for row in payload["rows"]} == {"Tim Satu", "Tim Dua"}

    result = await db_session.execute(
        select(Team).where(Team.event_id == event.id).order_by(Team.name)
    )
    teams = result.scalars().all()
    assert len(teams) == 2
    satu = next(t for t in teams if t.name == "Tim Satu")
    assert satu.education_level == "sd-mi"
    assert satu.sort_order == 3
    assert satu.school == "SMA Satu"
    dua = next(t for t in teams if t.name == "Tim Dua")
    assert dua.school is None
    assert dua.sort_order == 0


@pytest.mark.asyncio
async def test_confirm_wrong_hash_409(async_client, db_session):
    headers = await _admin_headers(async_client)
    event = await _event_with_category(db_session)
    await db_session.commit()

    content = _xlsx_bytes([["Tim A", None, "SD/MI", None, None]])
    res = await async_client.post(
        f"/api/v1/admin/events/{event.id}/teams/import/confirm",
        files={"file": ("peserta.xlsx", content, svc.XLSX_MIME)},
        data={"sha256": "0" * 64},
        headers=headers,
    )
    assert res.status_code == 409
    assert await _count_teams(db_session, event.id) == 0


@pytest.mark.asyncio
async def test_confirm_invalid_row_422_all_or_nothing(async_client, db_session):
    headers = await _admin_headers(async_client)
    event = await _event_with_category(db_session)
    await db_session.commit()

    content = _xlsx_bytes([
        ["Tim Baik", None, "SD/MI", None, None],
        ["Tim Buruk", None, "SMP/MTs", None, None],  # category != event category
    ])
    sha = hashlib.sha256(content).hexdigest()
    res = await async_client.post(
        f"/api/v1/admin/events/{event.id}/teams/import/confirm",
        files={"file": ("peserta.xlsx", content, svc.XLSX_MIME)},
        data={"sha256": sha},
        headers=headers,
    )
    assert res.status_code == 422
    body = res.json()
    assert body["success"] is False
    assert any(row["status"] != "valid" for row in body["data"]["rows"])
    assert await _count_teams(db_session, event.id) == 0


@pytest.mark.asyncio
async def test_confirm_event_without_category_422(async_client, db_session):
    headers = await _admin_headers(async_client)
    event = await create_event(db_session, EventCreate(
        name=f"Event {uuid.uuid4().hex[:8]}",
        category=None,
        status=EventStatus.VOTING_OPEN,
        price_per_vote=1000,
    ))
    await db_session.commit()

    content = _xlsx_bytes([["Tim A", None, "SD/MI", None, None]])
    res = await async_client.post(
        f"/api/v1/admin/events/{event.id}/teams/import/confirm",
        files={"file": ("peserta.xlsx", content, svc.XLSX_MIME)},
        data={"sha256": hashlib.sha256(content).hexdigest()},
        headers=headers,
    )
    assert res.status_code == 422
    assert "kategori" in res.json()["message"]
    assert await _count_teams(db_session, event.id) == 0


@pytest.mark.asyncio
async def test_confirm_existing_duplicate_422(async_client, db_session):
    headers = await _admin_headers(async_client)
    event = await _event_with_category(db_session)
    await create_team(db_session, TeamCreate(
        event_id=event.id, name="Tim Ada", education_level="sd-mi",
    ))
    await db_session.commit()

    content = _xlsx_bytes([["tim ada ", None, "SD/MI", None, None]])
    res = await async_client.post(
        f"/api/v1/admin/events/{event.id}/teams/import/confirm",
        files={"file": ("peserta.xlsx", content, svc.XLSX_MIME)},
        data={"sha256": hashlib.sha256(content).hexdigest()},
        headers=headers,
    )
    assert res.status_code == 422
    assert await _count_teams(db_session, event.id) == 1


@pytest.mark.asyncio
async def test_endpoints_require_admin(async_client, db_session):
    event = await _event_with_category(db_session)
    await db_session.commit()
    res = await async_client.post(
        f"/api/v1/admin/events/{event.id}/teams/import/preview",
        files={"file": ("p.xlsx", _xlsx_bytes([]), svc.XLSX_MIME)},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_preview_rejects_non_xlsx(async_client, db_session):
    headers = await _admin_headers(async_client)
    event = await _event_with_category(db_session)
    await db_session.commit()
    res = await async_client.post(
        f"/api/v1/admin/events/{event.id}/teams/import/preview",
        files={"file": ("data.csv", b"a,b\n1,2", "text/csv")},
        headers=headers,
    )
    assert res.status_code == 422
    assert ".xlsx" in res.json()["message"]


@pytest.mark.asyncio
async def test_concurrent_confirms_do_not_duplicate(async_client, db_session):
    """Two confirms with the same file: only the first may insert; the second sees 422."""
    headers = await _admin_headers(async_client)
    event = await _event_with_category(db_session)
    await db_session.commit()

    content = _xlsx_bytes([["Tim Race", None, "SD/MI", None, None]])
    sha = hashlib.sha256(content).hexdigest()
    first = await async_client.post(
        f"/api/v1/admin/events/{event.id}/teams/import/confirm",
        files={"file": ("peserta.xlsx", content, svc.XLSX_MIME)},
        data={"sha256": sha},
        headers=headers,
    )
    second = await async_client.post(
        f"/api/v1/admin/events/{event.id}/teams/import/confirm",
        files={"file": ("peserta.xlsx", content, svc.XLSX_MIME)},
        data={"sha256": sha},
        headers=headers,
    )
    assert first.status_code == 200
    assert second.status_code == 422
    assert await _count_teams(db_session, event.id) == 1
