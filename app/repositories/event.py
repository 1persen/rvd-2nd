"""
Event repository — all DB queries for events live here (no business logic).
"""
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event
from app.enums.event_status import EventStatus


async def get_event(session: AsyncSession, event_id: str) -> Optional[Event]:
    return await session.get(Event, event_id)


async def lock_event_category(session: AsyncSession, event_id: str) -> tuple[bool, Optional[str]]:
    """Row-lock the event until the caller's transaction ends.

    Returns (found, category). Used to serialize concurrent team imports per event.
    """
    stmt = select(Event.id, Event.category).where(Event.id == event_id).with_for_update()
    row = (await session.execute(stmt)).first()
    if row is None:
        return False, None
    return True, row.category


async def get_by_slug(session: AsyncSession, slug: str) -> Optional[Event]:
    # Slugs remain unique even for soft-deleted events because the database
    # unique index covers those rows too.
    stmt = select(Event).where(Event.slug == slug)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_active_event(session: AsyncSession) -> Optional[Event]:
    """Most recently created event with voting open status or current voting window."""
    now = datetime.now(timezone.utc)
    stmt = (
        select(Event)
        .where(
            Event.deleted_at.is_(None),
            or_(
                Event.status == EventStatus.VOTING_OPEN,
                and_(
                    Event.opens_at.is_not(None),
                    Event.closes_at.is_not(None),
                    Event.opens_at <= now,
                    Event.closes_at >= now,
                ),
            ),
        )
        .order_by(Event.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_events(
    session: AsyncSession, status: Optional[EventStatus] = None, limit: int = 100
) -> Sequence[Event]:
    stmt = select(Event).where(Event.deleted_at.is_(None)).order_by(Event.created_at.desc())
    if status is not None:
        stmt = stmt.where(Event.status == status)
    stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()
