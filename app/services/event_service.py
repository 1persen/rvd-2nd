"""
Event & team business logic (service layer) — public + admin CRUD.
Routers call these services; queries stay in repositories.
"""
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event
from app.enums.event_status import EventStatus
from app.models.team import Team
from app.repositories import event as event_repo
from app.repositories import team as team_repo
from app.schemas.event import EventCreate, EventUpdate
from app.schemas.team import TeamCreate, TeamUpdate
from app.utils import new_id
import re

from app.enums.education_level import (
    ALLOWED_EDUCATION_LEVELS,
    ALLOWED_EVENT_CATEGORIES,
    LEVEL_ORDER,
)

# Backwards-compatible aliases kept for existing callers/tests.
ALLOWED_EDUCATION_LEVELS = ALLOWED_EDUCATION_LEVELS
ALLOWED_EVENT_CATEGORIES = ALLOWED_EVENT_CATEGORIES

def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "event"

async def _unique_slug(session: AsyncSession, value: str) -> str:
    base = _slugify(value)
    candidate, suffix = base, 2
    while await event_repo.get_by_slug(session, candidate):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


async def get_active_event(session: AsyncSession) -> Event:
    event = await event_repo.get_active_event(session)
    if not event:
        raise HTTPException(status_code=404, detail="Belum ada event yang sedang berlangsung")
    return event


async def get_event_or_404(session: AsyncSession, event_id: str) -> Event:
    event = await event_repo.get_event(session, event_id)
    if not event or event.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Event tidak ditemukan")
    return event


async def require_voting_open(event: Event) -> None:
    """Business rule: vote hanya bisa dibuat saat event berstatus Voting Open."""
    if not event.is_voting_open:
        raise HTTPException(status_code=400, detail="Voting untuk event ini sedang ditutup")


async def get_team_or_404(session: AsyncSession, team_id: str, event_id: Optional[str] = None) -> Team:
    team = await team_repo.get_team(session, team_id)
    if not team or team.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Tim tidak ditemukan")
    if event_id is not None and team.event_id != event_id:
        raise HTTPException(status_code=404, detail="Tim tidak ditemukan pada event ini")
    return team


async def build_leaderboard(session: AsyncSession, event_id: str, limit: Optional[int] = None) -> dict:
    """Return leaderboard payload ordered by votes desc, with ranking."""
    from app.services.leaderboard_service import build_leaderboard as _build
    return await _build(session, event_id, limit)


async def build_donors(session: AsyncSession, event_id: str) -> dict:
    """Return donor list derived from successful payment records."""
    from app.repositories import payment as payment_repo
    from app.repositories import team as team_repo

    payments = await payment_repo.list_donations(session, event_id, limit=5000)

    teams = await team_repo.list_teams(session, event_id)
    team_map = {team.id: team.name for team in teams}

    donor_map = {}
    for supporter_name, team_id, amount, votes, created_at in payments:
        key = supporter_name.strip().lower()
        if not key:
            continue
        if key not in donor_map:
            donor_map[key] = {
                "name": supporter_name.strip(),
                "team_name": team_map.get(team_id, ""),
                "amount": 0,
                "votes": 0,
                "transactions": 0,
                "last_payment": created_at,
            }
        donor = donor_map[key]
        donor["amount"] += amount
        donor["votes"] += votes
        donor["transactions"] += 1
        if created_at and created_at > donor["last_payment"]:
            donor["last_payment"] = created_at
            donor["team_name"] = team_map.get(team_id, donor["team_name"])

    entries = sorted(donor_map.values(), key=lambda x: x["amount"], reverse=True)
    for i, entry in enumerate(entries, start=1):
        entry["rank"] = i
        entry["id"] = f"donor-{i}"
        entry["last_payment"] = entry["last_payment"].isoformat() if entry["last_payment"] else None

    return {"event_id": event_id, "entries": entries}


# --- Admin: Event CRUD ------------------------------------------------------
async def create_event(session: AsyncSession, payload: EventCreate) -> Event:
    if payload.category is not None and payload.category not in ALLOWED_EVENT_CATEGORIES:
        raise HTTPException(status_code=400, detail="Kategori event tidak valid")
    event = Event(
        id=new_id("evt"),
        name=payload.name,
        description=payload.description,
        slug=await _unique_slug(session, payload.name),
        category=payload.category,
        banner_url=payload.banner_url,
        timezone=payload.timezone,
        currency=payload.currency,
        max_vote_per_transaction=payload.max_vote_per_transaction,
        status=payload.status,
        opens_at=payload.opens_at,
        closes_at=payload.closes_at,
        price_per_vote=payload.price_per_vote,
    )
    session.add(event)
    await session.flush()
    return event


async def update_event(session: AsyncSession, event_id: str, payload: EventUpdate) -> Event:
    event = await get_event_or_404(session, event_id)
    data = payload.model_dump(exclude_unset=True)
    if "slug" in data and data["slug"] is None:
        data.pop("slug")
    if "category" in data and data["category"] is None:
        raise HTTPException(status_code=400, detail="Kategori event wajib dipilih")
    if data.get("category") is not None and data["category"] not in ALLOWED_EVENT_CATEGORIES:
        raise HTTPException(status_code=400, detail="Kategori event tidak valid")
    if "slug" in data:
        requested = data.pop("slug")
        if requested and requested != event.slug:
            data["slug"] = await _unique_slug(session, requested)
    for field, value in data.items():
        if value is not None or field in ("description", "opens_at", "closes_at", "slug", "banner_url", "timezone", "currency", "max_vote_per_transaction"):
            setattr(event, field, value)
    await session.flush()
    return event


async def soft_delete_event(session: AsyncSession, event_id: str) -> None:
    event = await get_event_or_404(session, event_id)
    event.deleted_at = new_datetime()
    await session.flush()


def new_datetime():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


# --- Admin: Team CRUD -------------------------------------------------------
async def create_team(session: AsyncSession, payload: TeamCreate) -> Team:
    await get_event_or_404(session, payload.event_id)
    # education_level boleh kosong: tim tanpa kategori tetap valid dan tidak
    # pernah masuk leaderboard kategori mana pun. Wajibnya kategori hanya
    # ditegakkan pada endpoint admin (lihat routers/events.py).
    if payload.education_level is not None and payload.education_level not in ALLOWED_EDUCATION_LEVELS:
        raise HTTPException(status_code=400, detail="Kategori pendidikan tidak valid")
    team = Team(
        id=new_id("tm"),
        event_id=payload.event_id,
        name=payload.name,
        school=payload.school,
        education_level=payload.education_level,
        description=payload.description,
        logo_url=payload.logo_url,
        sort_order=payload.sort_order,
    )
    session.add(team)
    await session.flush()
    return team


async def update_team(session: AsyncSession, event_id: str, team_id: str, payload: TeamUpdate) -> Team:
    team = await get_team_or_404(session, team_id, event_id)
    data = payload.model_dump(exclude_unset=True)
    if "education_level" in data:
        level = data.get("education_level")
        if level is not None and level not in ALLOWED_EDUCATION_LEVELS:
            raise HTTPException(status_code=400, detail="Kategori pendidikan tidak valid")
    for field, value in data.items():
        if value is not None or field in ("school", "description", "logo_url", "education_level"):
            setattr(team, field, value)
    await session.flush()
    return team


async def soft_delete_team(session: AsyncSession, event_id: str, team_id: str) -> None:
    team = await get_team_or_404(session, team_id, event_id)
    team.deleted_at = new_datetime()
    await session.flush()
