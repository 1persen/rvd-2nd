"""
Frontend page routes (SSR HTML via Jinja2).

Per CLAUDE.md rules: routers only receive requests and return responses.
Data berasal dari service + repository (database).
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.enums.event_status import EventStatus
from app.repositories import event as event_repo
from app.repositories import payment as payment_repo
from app.repositories import system_settings as settings_repo
from app.repositories import team as team_repo
from app.enums.education_level import EDUCATION_LABELS, LEVEL_ORDER
from app.services import event_page_service, event_service, leaderboard_service
from app.services.public_page_cache import cache_discovery_page

router = APIRouter()

templates = Jinja2Templates(directory="app/templates")
templates.env.globals["payment_sandbox_enabled"] = lambda: (
    not settings.midtrans_mock_enabled
    and settings.payments_configured
    and settings.DUITKU_ENVIRONMENT == "sandbox"
)

# Prebuilt Tailwind (scripts/build_css.sh) replaces the in-browser Play CDN when
# present; the mtime busts browser caches after a rebuild + restart.
_TAILWIND_CSS = Path("app/static/css/tailwind.css")
templates.env.globals["tailwind_css_version"] = (
    int(_TAILWIND_CSS.stat().st_mtime) if _TAILWIND_CSS.exists() else None
)

# public.css is versioned independently so edits to it bust browser caches even
# when tailwind.css is not rebuilt (e.g. on platforms that don't run build_css.sh).
_PUBLIC_CSS = Path("app/static/css/public.css")
templates.env.globals["public_css_version"] = (
    int(_PUBLIC_CSS.stat().st_mtime) if _PUBLIC_CSS.exists() else None
)

_INVITE_CSS = Path("app/static/css/invite.css")
templates.env.globals["invite_css_version"] = (
    int(_INVITE_CSS.stat().st_mtime) if _INVITE_CSS.exists() else None
)


_STATUS_LABELS = {
    EventStatus.DRAFT: "Draf",
    EventStatus.PUBLISHED: "Terbit",
    EventStatus.VOTING_OPEN: "Voting dibuka",
    EventStatus.VOTING_CLOSED: "Voting ditutup",
    EventStatus.FINISHED: "Selesai",
    EventStatus.ARCHIVED: "Diarsipkan",
}


async def _get_event_by_id_or_active(session: AsyncSession, event_id: str | None = None):
    """Ambil event by id; fallback ke voting_open terbaru; fallback ke event pertama."""
    if event_id:
        ev = await event_repo.get_event(session, event_id)
        if ev and ev.deleted_at is None:
            return ev
    ev = await event_repo.get_active_event(session)
    if ev:
        return ev
    events = await event_repo.list_events(session, limit=1)
    return events[0] if events else None


def _event_dict(event) -> dict:
    return event_page_service.event_dict(event)


def _event_with_stats_dict(event, total_teams=0, total_votes=0, total_supporters=0, progress=0) -> dict:
    return event_page_service.event_dict(
        event,
        total_teams=total_teams,
        total_votes=total_votes,
        total_supporters=total_supporters,
        progress=progress,
    )


def _team_dict(team, votes: int, rank: int) -> dict:
    return {
        "id": team.id,
        "rank": rank,
        "name": team.name,
        "school": team.school or "",
        "votes": votes,
        "trend": "same",
        "logo_url": getattr(team, "logo_url", None),
        "photo_url": getattr(team, "photo_url", None),
    }


@router.get("/")
@cache_discovery_page
async def landing(request: Request, session: AsyncSession = Depends(get_db)):
    events = await event_service.list_public_events(session, limit=50)
    event = next((ev for ev in events if ev.is_voting_open), events[0] if events else None)
    leaderboard = []
    if event:
        leaderboard = (await leaderboard_service.get_leaderboard(session, event.id))["entries"][:3]
    return templates.TemplateResponse(
        request,
        "pages/landing.html",
        {
            "event": _event_dict(event) if event else None,
            "all_events": [_event_dict(ev) for ev in events],
            "leaderboard": leaderboard,
        },
    )


@router.get("/events")
@cache_discovery_page
async def events_list(
    request: Request,
    event_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
):
    all_events_raw = await event_service.list_public_events(session, limit=50)
    event_ids = [ev.id for ev in all_events_raw]
    # Two aggregate queries for all events (was 2 queries + up to 5000 full
    # payment rows per event).
    team_stats = await team_repo.stats_by_event(session, event_ids)
    donor_counts = await payment_repo.count_donors_by_event(session, event_ids)

    events_with_stats = []
    for ev in all_events_raw:
        team_count, ev_total_votes = team_stats.get(ev.id, (0, 0))
        progress = 0
        if ev.price_per_vote and ev_total_votes > 0 and team_count > 0:
            progress = min(100, int((ev_total_votes / max(1, team_count * 100)) * 100))
        events_with_stats.append(
            _event_with_stats_dict(
                ev,
                total_teams=team_count,
                total_votes=ev_total_votes,
                total_supporters=donor_counts.get(ev.id, 0),
                progress=progress,
            )
        )

    # Legacy deep link: the catalog is now the only /events view, so a selected
    # event continues on its canonical invite URL instead of client-side switching.
    if event_id:
        selected = next((ev for ev in all_events_raw if ev.id == event_id), None)
        if selected is None:
            raise HTTPException(status_code=404, detail="Event tidak ditemukan")
        return RedirectResponse(
            _event_dict(selected)["invite_path"], status_code=302
        )

    return templates.TemplateResponse(
        request,
        "pages/events.html",
        {"all_events": events_with_stats},
    )


@router.get("/invite/{event_slug}")
async def invite_event(
    request: Request,
    event_slug: str,
    session: AsyncSession = Depends(get_db),
):
    """One public URL per event; the stored template decides how it renders.

    Drafts, archives, and soft-deleted events answer 404 here and in every
    public API, cache, and stream. Admins preview them through the admin API.
    """
    event = await event_service.get_public_event_by_slug_or_404(session, event_slug)

    team_stats = await team_repo.stats_by_event(session, [event.id])
    team_count, total_votes = team_stats.get(event.id, (0, 0))
    donor_counts = await payment_repo.count_donors_by_event(session, [event.id])

    context = event_page_service.build_invite_context(
        event,
        total_teams=team_count,
        total_votes=total_votes,
        total_supporters=donor_counts.get(event.id, 0),
        collections={},
    )
    return templates.TemplateResponse(request, context["template_path"], context)


@router.get("/events/{event_id}/vote/{team_id}")
async def vote_page(
    request: Request,
    event_id: str,
    team_id: str,
    session: AsyncSession = Depends(get_db),
):
    event = await event_service.get_public_event_or_404(session, event_id)
    team = await team_repo.get_team(session, team_id)
    if not team or team.deleted_at is not None or team.event_id != event_id:
        raise HTTPException(status_code=404, detail="Tim tidak ditemukan")

    if not event.is_voting_open:
        return templates.TemplateResponse(
            request,
            "pages/voting_closed.html",
            {"event": _event_dict(event)},
            status_code=200,
        )

    if not settings.payments_configured:
        return templates.TemplateResponse(
            request,
            "pages/payments_unavailable.html",
            {"event": _event_dict(event), "team": _team_dict(team, team.total_votes, 0)},
        )

    entries = (await leaderboard_service.get_leaderboard(session, event_id))["entries"]
    mine = next((e for e in entries if e["team_id"] == team_id), None)
    rank, votes = (mine["rank"], mine["votes"]) if mine else (1, team.total_votes)

    return templates.TemplateResponse(
        request,
        "pages/vote.html",
        {
            "event": _event_dict(event),
            "team": _team_dict(team, votes, rank),
        },
    )


@router.get("/leaderboard")
async def leaderboard_list(request: Request, session: AsyncSession = Depends(get_db)):
    all_events_raw = await event_service.list_public_events(session, limit=100)
    return templates.TemplateResponse(
        request,
        "pages/leaderboard.html",
        {
            "all_events": [_event_dict(ev) for ev in all_events_raw],
            "event": None,
            "leaderboard": [],
            "available_levels": [],
            "level": None,
            "labels": EDUCATION_LABELS,
        },
    )


@router.get("/leaderboard/{event_slug}")
async def leaderboard_detail(
    request: Request,
    event_slug: str,
    level: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
):
    event = await event_service.get_public_event_by_slug_or_404(session, event_slug)

    available_levels = await team_repo.get_available_levels(session, event.id)
    available_levels = sorted(set(available_levels), key=lambda x: LEVEL_ORDER.get(x, 99))
    chosen_level = level if level in available_levels else None
    if available_levels and chosen_level is None:
        chosen_level = available_levels[0]
    entries = (await leaderboard_service.get_leaderboard(session, event.id, level=chosen_level))["entries"] if available_levels else []

    return templates.TemplateResponse(
        request,
        "pages/leaderboard.html",
        {
            "event": _event_dict(event),
            "all_events": [],
            "leaderboard": entries,
            "available_levels": available_levels,
            "level": chosen_level,
            "labels": EDUCATION_LABELS,
        },
    )


@router.get("/support")
async def support(request: Request, session: AsyncSession = Depends(get_db)):
    contact = await settings_repo.get_bulk(
        session, ["support_email", "support_phone", "support_hours"]
    )
    return templates.TemplateResponse(
        request,
        "pages/support.html",
        {
            "support_email": contact.get("support_email", ""),
            "support_phone": contact.get("support_phone", ""),
            "support_hours": contact.get("support_hours", ""),
        },
    )


@router.get("/admin/login")
async def admin_login(request: Request, session: AsyncSession = Depends(get_db)):
    return templates.TemplateResponse(request, "pages/admin_login.html", {})


@router.get("/admin")
async def admin_dashboard(request: Request, session: AsyncSession = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "pages/admin_dashboard.html",
        {},
    )


@router.get("/admin/events")
async def admin_events(request: Request, session: AsyncSession = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "pages/admin_events.html",
        {},
    )

@router.get("/admin/participants")
async def admin_participants(request: Request, session: AsyncSession = Depends(get_db)):
    return templates.TemplateResponse(request, "pages/admin_participants.html", {})


@router.get("/admin/transactions")
async def admin_transactions(request: Request, session: AsyncSession = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "pages/admin_transactions.html",
        {},
    )


@router.get("/admin/audit-logs")
async def admin_audit_logs(request: Request, session: AsyncSession = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "pages/admin_audit_logs.html",
        {},
    )


@router.get("/admin/leaderboard")
async def admin_leaderboard(request: Request, session: AsyncSession = Depends(get_db)):
    event = await _get_event_by_id_or_active(session)
    entries = []
    if event:
        entries = (await event_service.build_leaderboard(session, event.id))["entries"]
    return templates.TemplateResponse(
        request,
        "pages/admin_leaderboard.html",
        {"event": _event_dict(event) if event else None, "leaderboard": entries},
    )


@router.get("/admin/settings")
async def admin_settings(request: Request, session: AsyncSession = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "pages/admin_settings.html",
        {},
    )


@router.get("/admin/progress")
async def admin_progress(request: Request, session: AsyncSession = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "pages/admin_progress.html",
        {},
    )
