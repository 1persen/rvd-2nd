"""
Leaderboard service â€” ranking, trend, cache and SSE push.

Read path: one query on teams (trend comes from teams.current_rank vs
teams.previous_rank), cached for CACHE_TTL seconds and refreshed on every push.
Write path: after a vote commits, schedule_push() coalesces pushes per event so
a burst of payments costs one rebuild + one broadcast per window.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session
from app.models.team import Team
from app.repositories import team as team_repo
from app.services import cache

logger = logging.getLogger(__name__)

# Strong references so pending push tasks are not garbage-collected mid-sleep.
_pending_pushes: set[asyncio.Task] = set()


def _trend(team: Team) -> str:
    if team.current_rank is None or team.previous_rank is None:
        return "same"
    if team.current_rank < team.previous_rank:
        return "up"
    if team.current_rank > team.previous_rank:
        return "down"
    return "same"


async def build_leaderboard(
    session: AsyncSession, event_id: str, level: Optional[str] = None, limit: Optional[int] = None
) -> dict:
    """Return leaderboard payload ordered by votes desc, with ranking (uncached)."""
    pairs = await team_repo.get_teams_with_votes(session, event_id, level=level)
    entries = [
        {
            "id": team.id,
            "rank": rank,
            "team_id": team.id,
            "name": team.name,
            "school": team.school,
            "votes": votes,
            "trend": _trend(team),
            "logo_url": team.logo_url,
        }
        for rank, (team, votes) in enumerate(pairs[:limit] if limit else pairs, start=1)
    ]
    return {
        "event_id": event_id,
        "entries": entries,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_leaderboard(session: AsyncSession, event_id: str, level: Optional[str] = None) -> dict:
    """Full ranking for public reads, served from cache when warm."""
    payload = await cache.get_cached_leaderboard(event_id, level=level)
    if payload is None:
        payload = await build_leaderboard(session, event_id, level=level)
        await cache.set_cached_leaderboard(event_id, payload, level=level)
    return payload


async def refresh_team_ranks(session: AsyncSession, event_id: str) -> None:
    """Store each team's new rank and the rank it had before this vote.

    The ORM only writes rows whose values actually changed, so a vote that does
    not move anyone touches no team row besides the voted one.
    """
    pairs = await team_repo.get_teams_with_votes(session, event_id)
    for rank, (team, _) in enumerate(pairs, start=1):
        team.previous_rank = team.current_rank
        team.current_rank = rank
    await session.flush()


async def publish_leaderboard(event_id: str) -> None:
    """Rebuild from committed data, refresh the cache and push to SSE clients.

    One message per view: the event-wide ranking (level=None) and one ranking
    per category that has teams. Every message carries its level, so a viewer
    of one category never receives the ranking of another category.
    """
    async with async_session() as session:
        levels = await team_repo.get_available_levels(session, event_id)
        views = [None, *levels]
        payloads = {}
        for level in views:
            payloads[level] = await build_leaderboard(session, event_id, level=level)
    for level, fresh in payloads.items():
        await cache.set_cached_leaderboard(event_id, fresh, level=level)
        await cache.publish(
            {"type": "leaderboard", "event_id": event_id, "level": level, "data": fresh}
        )


async def _push_later(event_id: str, delay: float) -> None:
    await asyncio.sleep(delay)
    try:
        await publish_leaderboard(event_id)
    except Exception:
        logger.exception("Leaderboard push failed for event %s", event_id)


async def schedule_push(event_id: str) -> None:
    """Call after a vote is COMMITTED.

    The first vote in a window schedules one push at the end of the window; the
    push rebuilds from the database then, so it includes every vote committed
    meanwhile. Later votes in the same window are no-ops.
    """
    interval = settings.LEADERBOARD_PUSH_INTERVAL_SECONDS
    if interval <= 0:
        await publish_leaderboard(event_id)
        return
    if not await cache.try_lock(f"wvc:lbpush:{event_id}", interval):
        return
    task = asyncio.create_task(_push_later(event_id, interval))
    _pending_pushes.add(task)
    task.add_done_callback(_pending_pushes.discard)


async def publish_support(session, payment) -> None:
    """Broadcast one confirmed support (ticker + effect) to public SSE clients.

    Call only after the payment's vote was granted in this request (voted_now).
    Contains only public fields: no email, phone, or Duitku references.
    """
    team = await session.get(Team, payment.team_id)
    await cache.publish({
        "type": "support",
        "event_id": payment.event_id,
        "level": None,
        "payment_id": payment.id,
        "supporter_name": "Anonim" if payment.is_anonymous else payment.supporter_name,
        "team_name": team.name if team else "",
        "votes": payment.votes,
        "support_message": payment.support_message or "",
        "effect_code": payment.effect_code,
    })
