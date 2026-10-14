"""Every public page renders, and every template a router names exists on disk."""
import re
import uuid
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

from app.main import app
from app.models.event import EventStatus
from app.services import event_service

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/events", "/leaderboard", "/support", "/admin/login"])
async def test_public_pages_render(path):
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path)
    assert response.status_code == 200, path


@pytest.mark.asyncio
async def test_leaderboard_page_honours_event_id(db_session):
    older = await event_service.create_event(
        db_session,
        event_service.EventCreate(name=f"Older {uuid.uuid4().hex[:8]}", status=EventStatus.VOTING_OPEN, price_per_vote=1000),
    )
    await event_service.create_event(  # newer, so it is the "active" fallback
        db_session,
        event_service.EventCreate(name=f"Newer {uuid.uuid4().hex[:8]}", status=EventStatus.VOTING_OPEN, price_per_vote=1000),
    )
    await db_session.commit()

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/leaderboard", params={"event_id": older.id})

    assert response.status_code == 200
    assert older.name in response.text


def test_every_template_named_in_routers_exists():
    named = {
        name
        for router in (ROOT / "app" / "routers").glob("*.py")
        for name in re.findall(r'"(pages/[\w/]+\.html)"', router.read_text())
    }
    missing = sorted(n for n in named if not (ROOT / "app" / "templates" / n).is_file())
    assert not missing, f"templates referenced but missing: {missing}"


@pytest.mark.asyncio
async def test_discovery_lists_published_events_without_drafts(db_session):
    suffix = uuid.uuid4().hex[:8]
    public = await event_service.create_event(
        db_session, event_service.EventCreate(name=f"Public discovery {suffix}", status=EventStatus.PUBLISHED, price_per_vote=1000),
    )
    draft = await event_service.create_event(
        db_session, event_service.EventCreate(name=f"Private draft {suffix}", status=EventStatus.DRAFT, price_per_vote=1000),
    )
    await db_session.commit()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in ("/", "/events"):
            response = await client.get(path)
            assert response.status_code == 200
            assert public.name in response.text
            # The catalog's initial selected-event payload must not leak a draft either.
            assert draft.name not in response.text


@pytest.mark.asyncio
async def test_support_page_has_no_placeholders():
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/support")
    assert response.status_code == 200
    assert "Pusat Bantuan" in response.text
    assert "Raja Voting Digital" in response.text
    assert "via Midtrans" not in response.text
    assert "Masukkan email resmi" not in response.text
    assert "Masukkan nomor kontak aktif" not in response.text
    assert "Masukkan jam operasional" not in response.text
    assert "Tentukan apakah pesanan" not in response.text
    assert "Ketentuan final yang perlu ditetapkan" not in response.text
    assert "Kebijakan Privasi" not in response.text


@pytest.mark.asyncio
async def test_checkout_without_gateway_does_not_collect_payer_details(test_data, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MIDTRANS_MOCK_MODE", False)
    monkeypatch.setattr(settings, "MIDTRANS_SERVER_KEY", "")
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/events/{test_data['event'].id}/vote/{test_data['team'].id}")
    assert response.status_code == 200
    assert "Pembayaran belum tersedia." in response.text
    assert 'id="voter-phone"' not in response.text
    assert "Simulasikan berhasil" not in response.text
