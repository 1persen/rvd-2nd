"""
Events & teams API — public read + admin CRUD.
Business logic lives in services; queries in repositories.
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.enums.education_level import ALLOWED_EDUCATION_LEVELS
from app.enums.event_status import EventStatus
from app.repositories import event as event_repo
from app.repositories import team as team_repo
from app.routers.deps import AdminUserJwt, DbSession
from app.schemas.common import ok
from app.schemas.event import EventCreate, EventOut, EventUpdate
from app.schemas.team import TeamCreate, TeamOut, TeamUpdate, TeamWithVotes
from app.services import cache, event_service, leaderboard_service
from app.utils import dumps

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


def _event_out(event) -> dict:
    data = EventOut.model_validate(event).model_dump()
    data["status_label"] = {
        EventStatus.DRAFT: "Draf",
        EventStatus.PUBLISHED: "Terbit",
        EventStatus.VOTING_OPEN: "Voting dibuka",
        EventStatus.VOTING_CLOSED: "Voting ditutup",
        EventStatus.FINISHED: "Selesai",
        EventStatus.ARCHIVED: "Diarsipkan",
    }.get(event.status, event.status.value)
    return data


# --- Public ----------------------------------------------------------------
@router.get("/events")
async def list_events(
    status: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
):
    status_enum = EventStatus(status) if status else None
    events = await event_repo.list_events(session, status=status_enum)
    return ok([_event_out(e) for e in events])


@router.get("/events/{event_id}")
async def get_event(event_id: str, session: AsyncSession = Depends(get_db)):
    event = await event_service.get_event_or_404(session, event_id)
    return ok(_event_out(event))


@router.get("/events/{event_id}/teams")
async def list_teams(event_id: str, level: str | None = Query(default=None), session: AsyncSession = Depends(get_db)):
    await event_service.get_event_or_404(session, event_id)
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
    key = f"wvc:donors:{event_id}"
    cached = await cache.cache_get(key)
    if cached:
        return ok(json.loads(cached))
    await event_service.get_event_or_404(session, event_id)
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
    # One cache entry per event holding the full ranking; `limit` only trims the
    # response. A warm cache answers without touching the database.
    payload = await cache.get_cached_leaderboard(event_id, level=level)
    if not payload:
        await event_service.get_event_or_404(session, event_id)
        payload = await leaderboard_service.get_leaderboard(session, event_id, level=level)
    return ok({
        **payload,
        "entries": payload["entries"][:limit],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


# --- Admin CRUD ------------------------------------------------------------
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
    return ok(_event_out(event), "Event berhasil dibuat")


@router.put("/admin/events/{event_id}")
async def update_event(
    event_id: str,
    payload: EventUpdate,
    request: Request,
    session: DbSession,
    _: AdminUserJwt,
):
    if "category" in payload.model_fields_set and payload.category is None:
        raise HTTPException(status_code=400, detail="Kategori event wajib dipilih")
    event = await event_service.update_event(session, event_id, payload)
    await _audit(session, request, "event.update", "event", event.id)
    await session.commit()
    return ok(_event_out(event), "Event berhasil diubah")


@router.delete("/admin/events/{event_id}")
async def delete_event(event_id: str, request: Request, session: DbSession, _: AdminUserJwt):
    await event_service.soft_delete_event(session, event_id)
    await _audit(session, request, "event.delete", "event", event_id)
    await session.commit()
    return ok(message="Event berhasil dihapus")


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
