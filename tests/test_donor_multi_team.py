"""Multi-team donor (Sultan tab) — donor who supported multiple teams shows all teams."""
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.enums.payment_gateway import PaymentGateway
from app.enums.payment_status import PaymentStatus
from app.models.payment import Payment
from app.models.team import Team
from app.services import event_service
from app.utils import new_id
from app.main import app


@pytest_asyncio.fixture
async def event_with_two_teams(db_session):
    """An event with two teams, both visible."""
    event = await event_service.create_event(
        db_session,
        event_service.EventCreate(
            name="Multi Team Event",
            status=event_service.EventStatus.VOTING_OPEN,
            price_per_vote=1000,
        ),
    )
    team1 = await event_service.create_team(
        db_session,
        event_service.TeamCreate(event_id=event.id, name="Team A", school="SMA Alpha"),
    )
    team2 = await event_service.create_team(
        db_session,
        event_service.TeamCreate(event_id=event.id, name="Team B", school="SMA Beta"),
    )
    await db_session.commit()
    return {"event": event, "team1": team1, "team2": team2}


async def _make_successful_payment(session, event_id, team_id, supporter_name, qty, created_at=None):
    """Insert a SUCCESS payment directly (bypasses invoice flow)."""
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    p = Payment(
        id=new_id("pay"),
        event_id=event_id,
        team_id=team_id,
        supporter_name=supporter_name,
        supporter_email="test@example.com",
        supporter_phone=f"081{uuid.uuid4().int % 10**8:08d}",
        is_anonymous=False,
        package_code="STD",
        package_label="Standar",
        qty=qty,
        amount=qty * 1000,
        votes=qty,
        vote_snapshot={"team": team_id, "votes": qty},
        status=PaymentStatus.SUCCESS,
        payment_gateway=PaymentGateway.MIDTRANS,
    )
    p.created_at = created_at
    p.webhook_received_at = created_at
    p.settled_at = created_at
    session.add(p)
    await session.flush()
    return p


@pytest.mark.asyncio
async def test_donor_with_multiple_teams_aggregated(event_with_two_teams, db_session):
    """One supporter who paid for two different teams shows both in team_names."""
    event = event_with_two_teams["event"]
    team1 = event_with_two_teams["team1"]
    team2 = event_with_two_teams["team2"]

    # Same supporter, two payments for different teams
    await _make_successful_payment(
        db_session, event.id, team1.id, "Supporter Multi", 10, created_at=__import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").timezone.utc)
    )
    await _make_successful_payment(
        db_session, event.id, team2.id, "Supporter Multi", 20, created_at=__import__("datetime").datetime(2026, 1, 2, tzinfo=__import__("datetime").timezone.utc)
    )
    await db_session.commit()

    donors = await event_service.build_donors(db_session, event.id)
    assert donors["entries"]
    entry = donors["entries"][0]
    assert entry["name"] == "Supporter Multi"
    assert entry["team_name"] == team2.name  # last payment wins
    # New field: team_names list (chronological order of first appearance = oldest first)
    assert "team_names" in entry
    # In our loop (newest-first), team_names will be [team2, team1] since we iterate newest-first
    # But due to dedupe it just appends in loop order. Let's assert both are present.
    assert team1.name in entry["team_names"]
    assert team2.name in entry["team_names"]
    assert len(entry["team_names"]) == 2


@pytest.mark.asyncio
async def test_donor_with_single_team_still_works(event_with_two_teams, db_session):
    """Backward compat: donor with only one team still has team_name and team_names=["team"]."""
    event = event_with_two_teams["event"]
    team1 = event_with_two_teams["team1"]

    await _make_successful_payment(
        db_session, event.id, team1.id, "Supporter Single", 15, created_at=__import__("datetime").datetime(2026, 1, 3, tzinfo=__import__("datetime").timezone.utc)
    )
    await db_session.commit()

    donors = await event_service.build_donors(db_session, event.id)
    entry = donors["entries"][0]
    assert entry["name"] == "Supporter Single"
    assert entry["team_name"] == team1.name
    assert entry["team_names"] == [team1.name]


@pytest.mark.asyncio
async def test_donor_api_returns_team_names(event_with_two_teams, db_session):
    """GET /api/v1/events/{id}/donors includes team_names in each entry."""
    event = event_with_two_teams["event"]
    team1 = event_with_two_teams["team1"]
    team2 = event_with_two_teams["team2"]

    await _make_successful_payment(
        db_session, event.id, team1.id, "API Supporter", 5, created_at=datetime(2026, 1, 5, tzinfo=timezone.utc)
    )
    await _make_successful_payment(
        db_session, event.id, team2.id, "API Supporter", 7, created_at=datetime(2026, 1, 6, tzinfo=timezone.utc)
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/events/{event.id}/donors")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["entries"]
    entry = data["entries"][0]
    assert entry["name"] == "API Supporter"
    assert "team_names" in entry
    assert team1.name in entry["team_names"]
    assert team2.name in entry["team_names"]