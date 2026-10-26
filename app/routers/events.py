"""
Events & teams API — public read + admin CRUD.
Business logic lives in services; queries in repositories.
"""
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.enums.education_level import ALLOWED_EDUCATION_LEVELS
from app.enums.event_status import EventStatus
from app.repositories import event as event_repo
from app.repositories import team as team_repo
from app.routers.deps import AdminUserJwt, DbSession
from app.schemas.common import ok
from app.schemas.event import AdminEventOut, EventCreate, EventOut, EventUpdate
from app.schemas.team import TeamCreate, TeamOut, TeamUpdate
from app.services import (
    cache,
    event_admin_service,
    event_asset_service,
    event_page_service,
    event_service,
    leaderboard_service,
)
from app.services.public_page_cache import invalidate_discovery_pages
from app.utils import dumps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["events"])


def _require_education_level(level: str | None) -> None:
    """Kategori pendidikan wajib dipilih dan valid — hanya untuk admin team CRUD.

    Alur publik (voting, checkout, webhook, settlement) tidak pernah memanggil
    validasi ini, sehingga tidak akan gagal karena kategori belum dikirim.
    """
    if level is None or not level.strip():
        raise HTTPException(status_code=400, detail="Kategori pendidikan wajib dipilih")
    if level not in ALLOWED_EDUCATION_LEVELS:
        raise HTTPException(status_code=400, detail="Kategori pendidikan tidak valid")


_STATUS_LABELS = {
    EventStatus.DRAFT: "Draf",
    EventStatus.PUBLISHED: "Terbit",
    EventStatus.VOTING_OPEN: "Voting dibuka",
    EventStatus.VOTING_CLOSED: "Voting ditutup",
    EventStatus.FINISHED: "Selesai",
    EventStatus.ARCHIVED: "Diarsipkan",
}


def _event_out(event) -> dict:
    data = EventOut.model_validate(event).model_dump()
    data["status_label"] = _STATUS_LABELS.get(event.status, event.status.value)
    return data


async def _admin_event_out(event) -> dict:
    """Admin read model; legacy/hand-edited theme JSON is normalized, never trusted."""
    data = AdminEventOut.model_validate(event).model_dump()
    data["status_label"] = _STATUS_LABELS.get(event.status, event.status.value)
    data["template_key"] = event_page_service.resolve_template_key(
        getattr(event, "template_key", None)
    )
    data["theme_config"] = event_page_service.normalize_theme_config(
        getattr(event, "theme_config", None)
    )
    data["assets"] = data["theme_config"].get("assets", {})
    data["storage_configured"] = event_page_service.settings.storage_configured
    data["preview_path"] = f"/admin/events/{event.id}/preview"
    data["public_url"] = f"/invite/{event.slug or event.id}"
    data["is_public"] = event_service.is_public_event(event)
    return data


async def _notify_presentation_change(
    event_id: str, was_public: bool, is_public_now: bool
) -> None:
    """Drop cached discovery HTML and close SSE streams after a visibility change."""
    await invalidate_discovery_pages()
    if was_public and not is_public_now:
        await cache.publish_event_visibility(event_id)


# --- Public ----------------------------------------------------------------
@router.get("/events")
async def list_events(
    status: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
):
    # A public filter can only narrow the public set; it can never open drafts
    # or archives. Unknown/private statuses return an empty list.
    if status:
        try:
            status_enum = EventStatus(status)
        except ValueError:
            return ok([])
        if status_enum not in event_service.PUBLIC_EVENT_STATUSES:
            return ok([])
        events = await event_repo.list_events(session, status=status_enum)
        events = [event for event in events if event.status in event_service.PUBLIC_EVENT_STATUSES]
    else:
        events = await event_service.list_public_events(session)
    return ok([_event_out(e) for e in events])


@router.get("/events/{event_id}")
async def get_event(event_id: str, session: AsyncSession = Depends(get_db)):
    event = await event_service.get_public_event_or_404(session, event_id)
    return ok(_event_out(event))


@router.get("/events/{event_id}/teams")
async def list_teams(event_id: str, level: str | None = Query(default=None), session: AsyncSession = Depends(get_db)):
    await event_service.get_public_event_or_404(session, event_id)
    pairs = await team_repo.get_teams_with_votes(session, event_id, level=level)
    result = []
    for rank, (team, votes) in enumerate(pairs, start=1):
        data = TeamOut.model_validate(team).model_dump()
        data.update({"votes": votes, "rank": rank})
        result.append(data)
    return ok(result)


@router.get("/events/{event_id}/donors")
async def list_donors(
    event_id: str,
    session: AsyncSession = Depends(get_db),
):
    # Visibility is checked before the cache so a cached donor list can never
    # keep an archived or draft event alive for anonymous visitors.
    await event_service.get_public_event_or_404(session, event_id)
    key = f"wvc:donors:{event_id}"
    cached = await cache.cache_get(key)
    if cached:
        return ok(json.loads(cached))
    payload = await event_service.build_donors(session, event_id)
    await cache.cache_set(key, dumps(payload))
    return ok(payload)


@router.get("/leaderboard")
async def leaderboard(
    event_id: str = Query(..., description="ID event"),
    level: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
):
    # Visibility is checked first: a warm ranking cache must not outlive the
    # event becoming a draft or archive.
    await event_service.get_public_event_or_404(session, event_id)
    # One cache entry per event holding the full ranking; `limit` only trims the
    # response. A warm cache answers without touching the database.
    payload = await cache.get_cached_leaderboard(event_id, level=level)
    if not payload:
        payload = await leaderboard_service.get_leaderboard(session, event_id, level=level)
    return ok({
        **payload,
        "entries": payload["entries"][:limit],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


# --- Admin CRUD ------------------------------------------------------------
async def _close_files(files: dict[str, UploadFile]) -> None:
    for upload in files.values():
        try:
            await upload.close()
        except Exception:
            pass


async def _parse_event_payload(request: Request, model=EventUpdate, allow_files: bool = True):
    """Read JSON or multipart Save/Preview into (payload, files)."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        raw = form.get("payload")
        files: dict[str, UploadFile] = {}
        if allow_files:
            for slot in event_asset_service.ASSET_SLOTS:
                upload = form.get(f"asset_{slot}")
                # Starlette's UploadFile is the base of fastapi.UploadFile, so an
                # isinstance check against the FastAPI subclass would miss it.
                if upload is not None and getattr(upload, "filename", None):
                    files[slot] = upload
        if not isinstance(raw, str):
            await _close_files(files)
            raise HTTPException(status_code=400, detail="Bagian payload wajib berisi JSON event")
        try:
            return model.model_validate_json(raw), files
        except ValidationError as exc:
            await _close_files(files)
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        data = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Body JSON event tidak valid") from exc
    try:
        return model.model_validate(data), {}
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/admin/events")
async def admin_list_events(session: DbSession, _: AdminUserJwt):
    events = await event_repo.list_events(session, limit=200)
    return ok([await _admin_event_out(event) for event in events])


@router.get("/admin/events/{event_id}")
async def admin_get_event(event_id: str, session: DbSession, _: AdminUserJwt):
    event = await event_service.get_event_or_404(session, event_id)
    return ok(await _admin_event_out(event))


@router.get("/admin/events/{event_id}/teams")
async def admin_list_event_teams(
    event_id: str,
    session: DbSession,
    _: AdminUserJwt,
    level: str | None = Query(default=None),
):
    await event_service.get_event_or_404(session, event_id)
    pairs = await team_repo.get_teams_with_votes(session, event_id, level=level)
    result = []
    for rank, (team, votes) in enumerate(pairs, start=1):
        data = TeamOut.model_validate(team).model_dump()
        data.update({"votes": votes, "rank": rank})
        result.append(data)
    return ok(result)


@router.get("/admin/events/{event_id}/leaderboard")
async def admin_event_leaderboard(
    event_id: str,
    session: DbSession,
    _: AdminUserJwt,
    level: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    await event_service.get_event_or_404(session, event_id)
    payload = await leaderboard_service.get_leaderboard(session, event_id, level=level)
    return ok({
        **payload,
        "entries": payload["entries"][:limit],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


@router.post("/admin/events")
async def create_event(
    payload: EventCreate,
    request: Request,
    session: DbSession,
    _: AdminUserJwt,
):
    if not payload.category:
        raise HTTPException(status_code=400, detail="Kategori event wajib dipilih")
    event = await event_service.create_event(session, payload)
    await _audit(session, request, "event.create", "event", event.id)
    await session.commit()
    await invalidate_discovery_pages()
    # New events start without assets; files are attached on the next Save once
    # the event has an id (the admin form reopens in edit mode).
    return ok(await _admin_event_out(event), "Event berhasil dibuat")


@router.put("/admin/events/{event_id}")
async def update_event(
    event_id: str,
    request: Request,
    session: DbSession,
    _: AdminUserJwt,
):
    previous = await event_service.get_event_or_404(session, event_id)
    was_public = event_service.is_public_event(previous)
    payload, files = await _parse_event_payload(request)
    try:
        if "category" in payload.model_fields_set and payload.category is None:
            raise HTTPException(status_code=400, detail="Kategori event wajib dipilih")
        try:
            result = await event_admin_service.save_event_with_assets(
                session, event_id, payload, files
            )
        except event_asset_service.AssetValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except event_asset_service.StorageUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except event_asset_service.StorageOperationError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        is_public_now = event_service.is_public_event(result.event)
        await _audit(session, request, "event.update", "event", event_id)
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            await event_asset_service.delete_assets(
                [asset.url for asset in result.uploaded], event_id
            )
            raise
        # Old objects are deleted only after the metadata commit succeeded.
        await event_asset_service.delete_assets(result.removed_urls, event_id)
        await _notify_presentation_change(event_id, was_public, is_public_now)
        return ok(await _admin_event_out(result.event), "Event berhasil diubah")
    finally:
        await _close_files(files)


@router.delete("/admin/events/{event_id}")
async def delete_event(event_id: str, request: Request, session: DbSession, _: AdminUserJwt):
    event = await event_service.get_event_or_404(session, event_id)
    was_public = event_service.is_public_event(event)
    await event_service.soft_delete_event(session, event_id)
    await _audit(session, request, "event.delete", "event", event_id)
    await session.commit()
    await _notify_presentation_change(event_id, was_public, False)
    return ok(message="Event berhasil dihapus")


@router.post("/admin/events/preview")
async def preview_event(request: Request, session: DbSession, _: AdminUserJwt):
    """Render the unsaved invite page; no database or Storage mutation."""
    from app.schemas.event import EventPreviewPayload

    payload, files = await _parse_event_payload(request, model=EventPreviewPayload)
    try:
        _, context, _ = await event_admin_service.build_preview(
            session, payload, files, payload.event_id
        )
    except event_asset_service.AssetValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except event_asset_service.StorageUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    finally:
        await _close_files(files)

    from app.routers.pages import templates

    html = templates.get_template(context["template_path"]).render(
        request=request, **context
    )
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex, nofollow",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'none'; script-src 'self'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' data: https://fonts.gstatic.com; "
                "img-src 'self' data: https:; connect-src 'none'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
            ),
        },
    )


@router.post("/admin/events/{event_id}/teams")
async def create_team(
    event_id: str,
    payload: TeamCreate,
    request: Request,
    session: DbSession,
    _: AdminUserJwt,
):
    payload.event_id = event_id
    _require_education_level(payload.education_level)
    team = await event_service.create_team(session, payload)
    await _audit(session, request, "team.create", "team", team.id)
    await session.commit()
    return ok(TeamOut.model_validate(team).model_dump(), "Tim berhasil dibuat")


@router.put("/admin/events/{event_id}/teams/{team_id}")
async def update_team(
    event_id: str,
    team_id: str,
    payload: TeamUpdate,
    request: Request,
    session: DbSession,
    _: AdminUserJwt,
):
    if payload.education_level is not None:
        _require_education_level(payload.education_level)
    team = await event_service.update_team(session, event_id, team_id, payload)
    await _audit(session, request, "team.update", "team", team.id)
    await session.commit()
    return ok(TeamOut.model_validate(team).model_dump(), "Tim berhasil diubah")


@router.delete("/admin/events/{event_id}/teams/{team_id}")
async def delete_team(
    event_id: str,
    team_id: str,
    request: Request,
    session: DbSession,
    _: AdminUserJwt,
):
    await event_service.soft_delete_team(session, event_id, team_id)
    await _audit(session, request, "team.delete", "team", team_id)
    await session.commit()
    return ok(message="Tim berhasil dihapus")

@router.get("/admin/teams")
async def list_admin_teams(
    session: DbSession,
    _: AdminUserJwt,
    event_id: str | None = Query(default=None),
):
    rows = await team_repo.list_all_with_events(session, event_id)
    result = []
    for team, event_name in rows:
        item = TeamOut.model_validate(team).model_dump()
        item["event_name"] = event_name
        result.append(item)
    return ok(result)


async def _audit(session, request, action, entity_type, entity_id):
    from app.services.audit_service import record

    await record(session, action=action, request=request, entity_type=entity_type, entity_id=entity_id)
