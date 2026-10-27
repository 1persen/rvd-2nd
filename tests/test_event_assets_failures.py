"""Asset upload failure paths, configuration guards, and preview errors.

Complements tests/test_event_presentation.py, which covers the happy path and
the visibility rules. Everything here runs without real Storage or payments.
"""
import io
import json
import uuid

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from app.core.config import settings
from app.enums.event_status import EventStatus
from app.main import app
from app.seeds.admin import seed_admin
from app.services import event_asset_service, event_page_service


def _client():
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def admin_headers():
    email = f"admin-{uuid.uuid4().hex[:8]}@test.local"
    password = uuid.uuid4().hex
    await seed_admin(email, password)
    async with _client() as client:
        login = await client.post("/api/v1/auth/admin/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


@pytest_asyncio.fixture
async def event_id():
    from app.core.database import async_session as session_factory
    from app.models.event import Event

    event_id = f"evt_up{uuid.uuid4().hex[:8]}"
    async with session_factory() as session:
        session.add(Event(
            id=event_id,
            name=f"Upload test {event_id}",
            slug=f"upload-{event_id}",
            status=EventStatus.PUBLISHED,
            category="sma-smk-ma",
            price_per_vote=1000,
        ))
        await session.commit()
    return event_id


def _png(width=40, height=30):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (20, 120, 90)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def storage_env(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setattr(settings, "SUPABASE_SECRET_KEY", "sb_secret_test")
    monkeypatch.setattr(settings, "SUPABASE_STORAGE_BUCKET", "event-assets")


@pytest.fixture
def storage_transport(monkeypatch):
    """Install a fake Storage transport; returns the list of recorded calls."""
    calls = []
    state = {"upload_status": 200, "raise": False}

    def handler(request):
        calls.append((request.method, request.url.path))
        if state["raise"]:
            raise httpx.ConnectError("storage down")
        return httpx.Response(state["upload_status"], json={})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        event_asset_service,
        "_client",
        lambda transport=None: original(transport=httpx.MockTransport(handler)),
    )
    calls_state = type("Calls", (), {"calls": calls, "state": state})
    return calls_state


def _multipart(payload=None, **files):
    data = {"payload": json.dumps(payload or {})}
    file_map = {
        name: (f"{name}.png", _png(), "image/png") for name in files
    }
    return {"data": data, "files": file_map}


# --- Configuration guards ---------------------------------------------------
@pytest.mark.parametrize("overrides", [
    {"SUPABASE_URL": "https://proj.supabase.co"},
    {"SUPABASE_SECRET_KEY": "sb_secret_x"},
    {"SUPABASE_URL": "http://proj.supabase.co", "SUPABASE_SECRET_KEY": "sb_secret_x"},
    {"SUPABASE_URL": "https://proj.supabase.co", "SUPABASE_SECRET_KEY": "not-a-secret-key"},
    {"SUPABASE_URL": "https://proj.supabase.co", "SUPABASE_SECRET_KEY": "sb_secret_x", "SUPABASE_STORAGE_BUCKET": "  "},
])
def test_partial_or_invalid_storage_configuration_fails_at_startup(overrides):
    from app.core.config import Settings

    base = {
        "SECRET_KEY": "x" * 32,
        "JWT_SECRET_KEY": "y" * 32,
        "SUPABASE_URL": "",
        "SUPABASE_SECRET_KEY": "",
        "SUPABASE_STORAGE_BUCKET": "event-assets",
    }
    with pytest.raises(RuntimeError):
        Settings(**{**base, **overrides})


def test_complete_storage_configuration_is_accepted():
    from app.core.config import Settings

    configured = Settings(
        SECRET_KEY="x" * 32,
        JWT_SECRET_KEY="y" * 32,
        SUPABASE_URL="https://proj.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_x",
    )
    assert configured.storage_configured is True
    disabled = Settings(SECRET_KEY="x" * 32, JWT_SECRET_KEY="y" * 32)
    assert disabled.storage_configured is False


# --- Storage failures -------------------------------------------------------
@pytest.mark.asyncio
async def test_storage_4xx_keeps_metadata_unchanged(admin_headers, event_id, storage_env, storage_transport):
    storage_transport.state["upload_status"] = 400
    async with _client() as client:
        before = (await client.get(f"/api/v1/admin/events/{event_id}", headers=admin_headers)).json()["data"]
        response = await client.put(
            f"/api/v1/admin/events/{event_id}",
            headers=admin_headers,
            data={"payload": json.dumps({"theme_config": {"background_type": "image"}})},
            files={"asset_background": ("bg.png", _png(600, 400), "image/png")},
        )
        assert response.status_code == 502, response.text
        after = (await client.get(f"/api/v1/admin/events/{event_id}", headers=admin_headers)).json()["data"]
    assert after["theme_config"]["assets"] == before["theme_config"]["assets"]
    assert after["theme_config"]["background_type"] == before["theme_config"]["background_type"]


@pytest.mark.asyncio
async def test_storage_network_error_is_reported_and_not_swallowed(admin_headers, event_id, storage_env, storage_transport):
    storage_transport.state["raise"] = True
    async with _client() as client:
        response = await client.put(
            f"/api/v1/admin/events/{event_id}",
            headers=admin_headers,
            data={"payload": json.dumps({})},
            files={"asset_logo": ("logo.png", _png(), "image/png")},
        )
    assert response.status_code == 502
    assert "supabase" not in response.text.lower()


@pytest.mark.asyncio
async def test_failed_second_upload_removes_the_first_object(admin_headers, event_id, storage_env, storage_transport):
    """Two files, second upload rejected: the first object must be compensated."""
    uploads = {"count": 0}

    def flaky(request):
        if request.method == "POST":
            uploads["count"] += 1
            if uploads["count"] == 2:
                return httpx.Response(500, json={})
            return httpx.Response(200, json={})
        storage_transport.calls.append((request.method, request.url.path))
        return httpx.Response(200, json={})

    original = httpx.AsyncClient
    event_asset_service._client = lambda transport=None: original(  # type: ignore[assignment]
        transport=httpx.MockTransport(flaky)
    )
    try:
        async with _client() as client:
            response = await client.put(
                f"/api/v1/admin/events/{event_id}",
                headers=admin_headers,
                data={"payload": json.dumps({})},
                files={
                    "asset_logo": ("logo.png", _png(), "image/png"),
                    "asset_background": ("bg.png", _png(600, 400), "image/png"),
                },
            )
            detail = (await client.get(f"/api/v1/admin/events/{event_id}", headers=admin_headers)).json()["data"]
    finally:
        event_asset_service._client = original  # type: ignore[assignment]

    assert response.status_code == 502
    # The first object was uploaded, so compensation must delete it.
    assert uploads["count"] == 2
    assert any(method == "DELETE" for method, _path in storage_transport.calls)
    assert detail["theme_config"]["assets"]["logo"] is None


@pytest.mark.asyncio
async def test_invalid_file_never_reaches_storage(admin_headers, event_id, storage_env, storage_transport):
    async with _client() as client:
        response = await client.put(
            f"/api/v1/admin/events/{event_id}",
            headers=admin_headers,
            data={"payload": json.dumps({})},
            files={"asset_ornament": ("x.svg", b"<svg><script>alert(1)</script></svg>", "image/svg+xml")},
        )
    assert response.status_code == 422
    assert storage_transport.calls == []


@pytest.mark.asyncio
async def test_soft_delete_does_not_delete_assets(admin_headers, event_id, storage_env, storage_transport):
    async with _client() as client:
        deleted = await client.delete(f"/api/v1/admin/events/{event_id}", headers=admin_headers)
    assert deleted.status_code == 200
    assert storage_transport.calls == []


# --- Raster validation ------------------------------------------------------
@pytest.mark.asyncio
async def test_corrupt_and_wrong_format_images_are_rejected():
    with pytest.raises(event_asset_service.AssetValidationError):
        await event_asset_service.validate_and_normalize("logo", b"\x89PNG\r\n\x1a\nnot-really-a-png")
    with pytest.raises(event_asset_service.AssetValidationError):
        await event_asset_service.validate_and_normalize("logo", b"GIF89a" + b"\x00" * 40)
    with pytest.raises(event_asset_service.AssetValidationError):
        await event_asset_service.validate_and_normalize("logo", b"")


@pytest.mark.asyncio
async def test_decompression_bomb_is_rejected():
    """A decode bomb is either stopped by Pillow's own error or by our pixel cap."""
    from PIL import Image

    from app.core.config import settings  # noqa: F401  (import keeps app loaded)

    assert Image.MAX_IMAGE_PIXELS == event_asset_service._MAX_PIXELS
    width, height = 9000, 4000
    assert width * height > event_asset_service._MAX_PIXELS

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (0, 0, 0)).save(buffer, format="PNG")
    with pytest.raises(event_asset_service.AssetValidationError) as error:
        await event_asset_service.validate_and_normalize("background", buffer.getvalue())
    assert "megapiksel" in str(error.value)


@pytest.mark.asyncio
async def test_animated_or_oversized_input_is_bounded():
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), (0, 0, 0)).save(buffer, format="PNG")
    oversized = buffer.getvalue() + b"\x00" * (5 * 1024 * 1024 + 1)
    with pytest.raises(event_asset_service.AssetValidationError):
        await event_asset_service.validate_and_normalize("poster", oversized)


# --- Preview errors ---------------------------------------------------------
@pytest.mark.asyncio
async def test_preview_rejects_invalid_theme_with_422(admin_headers):
    async with _client() as client:
        response = await client.post(
            "/api/v1/admin/events/preview",
            headers=admin_headers,
            json={"name": "Pratinjau", "theme_config": {"primary_color": "red"}},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_preview_rejects_broken_file_with_422(admin_headers):
    async with _client() as client:
        response = await client.post(
            "/api/v1/admin/events/preview",
            headers=admin_headers,
            data={"payload": json.dumps({"name": "Pratinjau"})},
            files={"asset_logo": ("logo.png", b"not-an-image", "image/png")},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_preview_requires_a_payload_part(admin_headers):
    async with _client() as client:
        response = await client.post(
            "/api/v1/admin/events/preview",
            headers=admin_headers,
            data={"other": "value"},
            files={"asset_logo": ("logo.png", _png(), "image/png")},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_preview_of_existing_event_uses_live_data_but_writes_nothing(
    admin_headers, event_id, storage_env, storage_transport
):
    async with _client() as client:
        response = await client.post(
            "/api/v1/admin/events/preview",
            headers=admin_headers,
            json={"event_id": event_id, "name": "Nama belum disimpan", "template_key": "cinematic"},
        )
        assert response.status_code == 200, response.text
        detail = (await client.get(f"/api/v1/admin/events/{event_id}", headers=admin_headers)).json()["data"]

    assert "Nama belum disimpan" in response.text
    assert "cinematic-title" in response.text
    assert detail["name"] != "Nama belum disimpan"
    assert storage_transport.calls == []


@pytest.mark.asyncio
async def test_preview_upload_becomes_data_url_without_persisting(
    admin_headers, event_id, storage_env, storage_transport
):
    async with _client() as client:
        response = await client.post(
            "/api/v1/admin/events/preview",
            headers=admin_headers,
            data={"payload": json.dumps({"event_id": event_id, "theme_config": {"background_type": "image"}})},
            files={"asset_background": ("bg.png", _png(800, 500), "image/png")},
        )
        assert response.status_code == 200, response.text
        detail = (await client.get(f"/api/v1/admin/events/{event_id}", headers=admin_headers)).json()["data"]

    assert "data:image/webp;base64," in response.text
    assert detail["theme_config"]["assets"]["background"] is None
    assert storage_transport.calls == []


# --- Theme normalization on read -------------------------------------------
def test_legacy_theme_values_are_normalized_instead_of_failing():
    theme = event_page_service.normalize_theme_config({
        "background_type": "url(javascript:alert(1))",
        "primary_color": "#abc",
        "background_overlay": -3,
        "show_participants": "yes",
        "assets": "not-a-dict",
    })
    assert theme["background_type"] == "default"
    assert theme["primary_color"] == event_page_service.DEFAULT_THEME["primary_color"]
    assert theme["background_overlay"] == 0.0
    assert theme["show_participants"] is True
    assert theme["assets"]["logo"] is None