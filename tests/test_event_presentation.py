"""Event presentation: themes, visibility guards, uploads, and preview."""
import io
import json
import time
import uuid

import httpx
import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport

from app.core.config import settings
from app.enums.event_status import EventStatus
from app.main import app
from app.seeds.admin import seed_admin
from app.services import event_page_service, public_page_cache


@pytest_asyncio.fixture
async def admin_headers():
    email = f"admin-{uuid.uuid4().hex[:8]}@test.local"
    password = uuid.uuid4().hex
    await seed_admin(email, password)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/v1/auth/admin/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def _client():
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _theme(**overrides):
    theme = {
        "background_type": "solid",
        "background_color": "#160F0A",
        "gradient_start": "#160F0A",
        "gradient_end": "#3A270F",
        "background_overlay": 0.5,
        "primary_color": "#D4A036",
        "secondary_color": "#160F0A",
        "text_color": "#FDFAF5",
        "button_text_color": "#160F0A",
        "show_countdown": True,
        "show_participants": True,
    }
    theme.update(overrides)
    return theme


def _png(width=40, height=30, color=(200, 30, 30)):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


async def _make_event(event_id, slug, status=EventStatus.PUBLISHED, template="classic", theme=None) -> None:
    from app.core.database import async_session as session_factory
    from app.models.event import Event

    async with session_factory() as session:
        session.add(Event(
            id=event_id,
            name=f"Event {slug}",
            slug=slug,
            status=status,
            category="sma-smk-ma",
            price_per_vote=1000,
            template_key=template,
            theme_config=theme or {},
        ))
        await session.commit()


@pytest.fixture
def event_ids():
    suffix = uuid.uuid4().hex[:8]
    return {
        "draft": f"evt_draft{suffix}",
        "archived": f"evt_arch{suffix}",
        "live": f"evt_live{suffix}",
        "deleted": f"evt_del{suffix}",
        "suffix": suffix,
    }


@pytest_asyncio.fixture
async def seeded_events(event_ids):
    await _make_event(event_ids["draft"], f"draft-{event_ids['suffix']}", EventStatus.DRAFT)
    await _make_event(event_ids["archived"], f"arc-{event_ids['suffix']}", EventStatus.ARCHIVED)
    await _make_event(event_ids["live"], f"live-{event_ids['suffix']}", EventStatus.PUBLISHED, "poster", _theme())
    await _make_event(event_ids["deleted"], f"del-{event_ids['suffix']}", EventStatus.PUBLISHED)
    from datetime import datetime, timezone

    from app.core.database import async_session as session_factory
    from app.models.event import Event as EventModel

    async with session_factory() as session:
        event = await session.get(EventModel, event_ids["deleted"])
        event.deleted_at = datetime.now(timezone.utc)
        await session.commit()
    public_page_cache._pages.clear()
    return event_ids


# --- Public visibility ------------------------------------------------------
@pytest.mark.asyncio
async def test_invite_404_for_draft_archive_and_soft_deleted(seeded_events):
    async with _client() as client:
        for key in ("draft", "archived", "deleted"):
            event_id = seeded_events[key]
            for path in (
                f"/invite/{event_id}",
                f"/api/v1/events/{event_id}",
                f"/api/v1/events/{event_id}/teams",
                f"/api/v1/events/{event_id}/donors",
                f"/api/v1/leaderboard?event_id={event_id}",
                f"/api/v1/leaderboard/stream?event_id={event_id}",
                f"/events/{event_id}/vote/tm_missing",
                f"/leaderboard/{event_id}",
            ):
                response = await client.get(path)
                assert response.status_code == 404, (key, path, response.status_code)
                if path.startswith("/api/"):
                    assert "Event" not in response.text or "tidak ditemukan" in response.text


@pytest.mark.asyncio
async def test_public_event_list_never_includes_private_events(seeded_events):
    async with _client() as client:
        response = await client.get("/api/v1/events?status=DRAFT")
        assert response.status_code == 200
        assert response.json()["data"] == []
        listing = (await client.get("/api/v1/events")).json()["data"]
        ids = {item["id"] for item in listing}
        assert seeded_events["live"] in ids
        assert not ids & {seeded_events["draft"], seeded_events["archived"], seeded_events["deleted"]}


@pytest.mark.asyncio
async def test_invite_renders_stored_template_for_public_event(seeded_events):
    async with _client() as client:
        response = await client.get(f"/invite/{seeded_events['live']}")
    assert response.status_code == 200
    html = response.text
    assert 'data-template="poster"' in html
    assert "--event-primary:#D4A036" in html
    assert "poster-image" in html


@pytest.mark.asyncio
async def test_legacy_events_event_id_redirects_to_invite(seeded_events):
    async with _client() as client:
        response = await client.get(f"/events?event_id={seeded_events['live']}", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == f"/invite/live-{seeded_events['suffix']}"
        missing = await client.get(f"/events?event_id={seeded_events['draft']}", follow_redirects=False)
        assert missing.status_code == 404


def test_theme_registry_rejects_unknown_templates():
    assert event_page_service.resolve_template_key("cinematic") == "cinematic"
    assert event_page_service.resolve_template_key("../../etc/passwd") == "classic"
    assert event_page_service.resolve_template_key(None) == "classic"


def test_theme_normalization_drops_unsafe_values():
    theme = event_page_service.normalize_theme_config({
        "primary_color": "red; background:url(javascript:alert(1))",
        "text_color": "#ABCDEF",
        "background_overlay": 5,
        "assets": {"logo": "javascript:alert(1)", "poster": "https://example.com/x.png"},
    })
    assert theme["primary_color"] == event_page_service.DEFAULT_THEME["primary_color"]
    assert theme["text_color"] == "#ABCDEF"
    assert theme["background_overlay"] == 0.85
    assert theme["assets"]["logo"] is None
    assert theme["assets"]["poster"] == "https://example.com/x.png"


# --- Admin read + save ------------------------------------------------------
@pytest.mark.asyncio
async def test_admin_can_read_and_preview_drafts(admin_headers, seeded_events):
    async with _client() as client:
        detail = await client.get(f"/api/v1/admin/events/{seeded_events['draft']}", headers=admin_headers)
        assert detail.status_code == 200
        data = detail.json()["data"]
        assert data["is_public"] is False
        assert data["template_key"] == "classic"
        assert data["theme_config"]["primary_color"] == "#D4A036"

        listing = await client.get("/api/v1/admin/events", headers=admin_headers)
        assert listing.status_code == 200
        assert seeded_events["draft"] in {item["id"] for item in listing.json()["data"]}

        preview = await client.post(
            "/api/v1/admin/events/preview",
            headers=admin_headers,
            json={
                "name": "Pratinjau Draf",
                "template_key": "cinematic",
                "theme_config": _theme(background_type="gradient"),
                "status": "DRAFT",
            },
        )
        assert preview.status_code == 200, preview.text
        assert "Pratinjau" in preview.text
        assert "cinematic-title" in preview.text
        assert preview.headers["cache-control"] == "no-store"
        assert preview.headers["x-robots-tag"].startswith("noindex")


@pytest.mark.asyncio
async def test_admin_save_json_updates_presentation_and_rejects_bad_theme(admin_headers, seeded_events):
    async with _client() as client:
        ok = await client.put(
            f"/api/v1/admin/events/{seeded_events['live']}",
            headers=admin_headers,
            json={"template_key": "cinematic", "theme_config": _theme(show_participants=False)},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["data"]["template_key"] == "cinematic"

        page = await client.get(f"/invite/{seeded_events['live']}")
        assert 'data-template="cinematic"' in page.text
        assert "PESERTA" not in page.text

        low_contrast = await client.put(
            f"/api/v1/admin/events/{seeded_events['live']}",
            headers=admin_headers,
            json={"theme_config": _theme(text_color="#000000", background_color="#111111")},
        )
        assert low_contrast.status_code == 422

        unknown_key = await client.put(
            f"/api/v1/admin/events/{seeded_events['live']}",
            headers=admin_headers,
            json={"theme_config": {**_theme(), "custom_css": "body{display:none}"}},
        )
        assert unknown_key.status_code == 422

        foreign_asset = await client.put(
            f"/api/v1/admin/events/{seeded_events['live']}",
            headers=admin_headers,
            json={"theme_config": {**_theme(), "assets": {"logo": "https://example.com/logo.png"}}},
        )
        assert foreign_asset.status_code == 400


@pytest.mark.asyncio
async def test_upload_rejected_with_503_when_storage_is_not_configured(admin_headers, seeded_events, monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", "")
    monkeypatch.setattr(settings, "SUPABASE_SECRET_KEY", "")
    async with _client() as client:
        response = await client.put(
            f"/api/v1/admin/events/{seeded_events['live']}",
            headers=admin_headers,
            data={"payload": json.dumps({"theme_config": _theme()})},
            files={"asset_logo": ("logo.png", _png(), "image/png")},
        )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_upload_normalizes_stores_and_replaces_assets(admin_headers, seeded_events, monkeypatch):
    """Upload goes through validation and the Storage API (mocked transport)."""
    from PIL import Image

    monkeypatch.setattr(settings, "SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setattr(settings, "SUPABASE_SECRET_KEY", "sb_secret_test")
    monkeypatch.setattr(settings, "SUPABASE_STORAGE_BUCKET", "event-assets")

    calls = []

    def storage(request):
        calls.append((request.method, request.url.path, dict(request.headers)))
        if request.method == "POST":
            return httpx.Response(200, json={"Key": request.url.path})
        return httpx.Response(200, json={})

    from app.services import event_asset_service

    original = httpx.AsyncClient
    monkeypatch.setattr(
        event_asset_service,
        "_client",
        lambda transport=None: original(transport=httpx.MockTransport(storage)),
    )

    event_id = seeded_events["live"]
    with Image.open(io.BytesIO(_png(1200, 900))) as img:
        img.info["exif"] = b""
    async with _client() as client:
        response = await client.put(
            f"/api/v1/admin/events/{event_id}",
            headers=admin_headers,
            data={"payload": json.dumps({"theme_config": _theme(background_type="image")})},
            files={"asset_background": ("bg.png", _png(1200, 900), "image/png")},
        )
        assert response.status_code == 200, response.text
        assets = response.json()["data"]["assets"]
        assert assets["background"].startswith(
            "https://proj.supabase.co/storage/v1/object/public/event-assets/events/"
        )
        upload = next(call for call in calls if call[0] == "POST")
        assert upload[2]["x-upsert"] == "false"
        assert upload[2]["apikey"] == "sb_secret_test"
        assert "authorization" not in upload[2]
        assert "events/" + event_id + "/background-" in upload[1]

        page = await client.get(f"/invite/{event_id}")
        assert assets["background"] in page.text
        assert 'data-bg="image"' in page.text

        # Replacing the background deletes only the previous object, after commit.
        calls.clear()
        replaced = await client.put(
            f"/api/v1/admin/events/{event_id}",
            headers=admin_headers,
            data={"payload": json.dumps({"theme_config": _theme(background_type="image")})},
            files={"asset_background": ("bg2.png", _png(800, 600, (10, 100, 200)), "image/png")},
        )
        assert replaced.status_code == 200, replaced.text
        deletes = [call for call in calls if call[0] == "DELETE"]
        assert len(deletes) == 1
        assert "background-" in json.dumps(deletes[0][1]) or "storage/v1/object/event-assets" in deletes[0][1]

        cleared = await client.put(
            f"/api/v1/admin/events/{event_id}",
            headers=admin_headers,
            json={"theme_config": {**_theme(background_type="default"), "assets": {}}},
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["data"]["assets"]["background"] is None


# --- Asset validation (pure) -----------------------------------------------
@pytest.mark.asyncio
async def test_raster_uploads_are_reencoded_and_metadata_stripped():
    from PIL import Image
    from app.services import event_asset_service as assets

    buffer = io.BytesIO()
    image = Image.new("RGB", (2400, 1600), (10, 90, 160))
    exif = Image.Exif()
    exif[0x010F] = "TestCamera"
    image.save(buffer, format="JPEG", exif=exif)
    normalized = await assets.validate_and_normalize("background", buffer.getvalue())

    assert normalized.content_type == "image/webp"
    with Image.open(io.BytesIO(normalized.data)) as encoded:
        assert encoded.format == "WEBP"
        assert max(encoded.size) <= 1920
        assert not encoded.getexif()


@pytest.mark.asyncio
async def test_svg_ornament_rejects_scripts_and_external_references():
    from app.services import event_asset_service as assets

    good = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="#fff"/></svg>'
    normalized = await assets.validate_and_normalize("ornament", good)
    assert normalized.content_type == "image/svg+xml"

    for bad in (
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://evil.test/x.png"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>',
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><svg>&e;</svg>',
        b"<html></html>",
    ):
        with pytest.raises(assets.AssetValidationError):
            await assets.validate_and_normalize("ornament", bad)


@pytest.mark.asyncio
async def test_font_requires_valid_woff2_magic():
    from app.services import event_asset_service as assets

    with pytest.raises(assets.AssetValidationError):
        await assets.validate_and_normalize("font", b"not-a-font")
    with pytest.raises(assets.AssetValidationError):
        await assets.validate_and_normalize("font", b"wOF2" + b"\x00" * 300)


@pytest.mark.asyncio
async def test_oversized_admin_body_is_rejected_before_parsing(admin_headers):
    async with _client() as client:
        response = await client.put(
            "/api/v1/admin/events/whatever",
            headers={**admin_headers, "Content-Type": "multipart/form-data; boundary=x"},
            content=b"x" * (17 * 1024 * 1024 + 10),
        )
    assert response.status_code == 413


# --- Cache + stream visibility ---------------------------------------------
@pytest.mark.asyncio
async def test_archiving_closes_streams_and_drops_discovery_cache(admin_headers, seeded_events):
    from app.services import cache

    event_id = seeded_events["live"]
    queue = cache.asyncio.Queue()
    cache._subscribers[(event_id, "sma-smk-ma")].add(queue)
    try:
        async with _client() as client:
            before = await client.get(f"/api/v1/events/{event_id}")
            assert before.status_code == 200
            archived = await client.put(
                f"/api/v1/admin/events/{event_id}",
                headers=admin_headers,
                json={"status": "ARCHIVED"},
            )
            assert archived.status_code == 200
            after = await client.get(f"/api/v1/events/{event_id}")
            assert after.status_code == 404
            page = await client.get(f"/invite/{event_id}")
            assert page.status_code == 404
        frame = queue.get_nowait()
        assert "event_visibility" in frame
    finally:
        cache._subscribers[(event_id, "sma-smk-ma")].discard(queue)


@pytest.mark.asyncio
async def test_checkout_rejects_new_invoice_for_non_public_event(test_data, db_session):
    event = test_data["event"]
    event.status = EventStatus.ARCHIVED
    await db_session.commit()
    async with _client() as client:
        response = await client.post("/api/v1/payments", json={
            "event_id": event.id,
            "team_id": test_data["team"].id,
            "qty": 1,
            "supporter_name": "Voter",
            "supporter_phone": f"0815{uuid.uuid4().int % 10**8:08d}",
        })
    assert response.status_code == 404
    event.status = EventStatus.VOTING_OPEN
    await db_session.commit()


def test_admin_tokens_are_required_for_presentation_endpoints():
    """Guard against accidentally opening the admin event endpoints."""
    assert time.time() > 0
    forged = jwt.encode({"sub": "usr_none"}, "wrong", algorithm=settings.JWT_ALGORITHM)

    async def call():
        async with _client() as client:
            for method, path, kwargs in (
                ("get", "/api/v1/admin/events", {}),
                ("post", "/api/v1/admin/events/preview", {"json": {}}),
                ("put", "/api/v1/admin/events/evt_x", {"json": {}}),
            ):
                no_token = await getattr(client, method)(path, **kwargs)
                assert no_token.status_code == 401, path
                bad_token = await getattr(client, method)(path, headers={"Authorization": f"Bearer {forged}"}, **kwargs)
                assert bad_token.status_code == 401, path

    import asyncio

    asyncio.run(call())
