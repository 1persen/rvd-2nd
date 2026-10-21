"""Voting under load: parallel settlements must neither lose nor duplicate votes,
a burst of votes is pushed to SSE clients once, and pushes only reach viewers of
their own event."""
import asyncio

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import async_session
from app.enums.payment_gateway import PaymentGateway
from app.enums.payment_status import PaymentStatus
from app.middleware.rate_limit import _bucket_name
from app.models.payment import Payment
from app.models.team import Team
from app.repositories import payment as payment_repo
from app.models.vote_log import VoteLog
from app.services import cache, leaderboard_service, payment_service


async def _create(async_client, test_data, phone, qty):
    response = await async_client.post(
        "/api/v1/payments",
        json={
            "event_id": test_data["event"].id,
            "team_id": test_data["team"].id,
            "qty": qty,
            "supporter_name": "Voter",
            "supporter_phone": phone,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _settle(payment) -> bool:
    """One webhook delivery on its own connection, like a separate worker would."""
    notification = {
        "order_id": payment["id"],
        "transaction_status": "settlement",
        "status_code": "200",
        "gross_amount": f"{payment['amount']}.00",
    }
    async with async_session() as session:
        _, voted_now = await payment_service.process_webhook(session, notification)
        await session.commit()
        return voted_now


async def _team_votes(team_id):
    async with async_session() as session:
        return (await session.execute(select(Team.total_votes).where(Team.id == team_id))).scalar_one()


@pytest.mark.asyncio
async def test_duplicate_parallel_webhooks_grant_votes_once(async_client, test_data):
    payment = await _create(async_client, test_data, "081300000001", qty=3)

    results = await asyncio.gather(*[_settle(payment) for _ in range(5)])

    assert results.count(True) == 1
    assert await _team_votes(test_data["team"].id) == 3
    async with async_session() as session:
        logs = await session.execute(
            select(func.count()).select_from(VoteLog).where(VoteLog.payment_id == payment["id"])
        )
        assert logs.scalar_one() == 1


@pytest.mark.asyncio
async def test_parallel_settlements_for_one_team_lose_no_votes(async_client, test_data):
    payments = [
        await _create(async_client, test_data, f"0814000000{i:02d}", qty=i + 1) for i in range(8)
    ]

    await asyncio.gather(*[_settle(p) for p in payments])

    assert await _team_votes(test_data["team"].id) == sum(p["votes"] for p in payments)


@pytest.mark.asyncio
async def test_settlement_is_not_blocked_by_an_invoice_still_in_flight(async_client, test_data):
    """An uncommitted invoice insert (waiting on the gateway) holds KEY SHARE on the
    event row through its FK; the per-event vote lock must not wait for it."""
    to_settle = await _create(async_client, test_data, "081600000001", qty=1)

    async with async_session() as in_flight:
        in_flight.add(Payment(
            event_id=test_data["event"].id, team_id=test_data["team"].id, qty=1, amount=1000,
            votes=1, package_code="custom", package_label="1 vote", vote_snapshot={},
            supporter_name="Slow", supporter_phone="081600000002",
            status=PaymentStatus.PENDING, payment_gateway=PaymentGateway.MIDTRANS,
        ))
        await in_flight.flush()  # FK lock taken, transaction left open

        assert await asyncio.wait_for(_settle(to_settle), timeout=3) is True
        await in_flight.rollback()


@pytest.mark.asyncio
async def test_status_poll_never_overwrites_a_settled_payment(async_client, test_data):
    created = await _create(async_client, test_data, "081700000001", qty=1)
    async with async_session() as poll:
        stale = await payment_repo.get_payment(poll, created["id"])  # read while PENDING
        assert stale.status == PaymentStatus.PENDING
        assert await _settle(created) is True  # webhook commits SUCCESS meanwhile

        await payment_repo.expire_if_pending(poll, created["id"])
        await poll.commit()
        await poll.refresh(stale)
        assert stale.status == PaymentStatus.SUCCESS


@pytest.mark.asyncio
async def test_burst_of_votes_is_pushed_once_per_window(monkeypatch, test_data):
    monkeypatch.setattr(settings, "LEADERBOARD_PUSH_INTERVAL_SECONDS", 0.2)
    sent = []

    async def capture(payload):
        sent.append(payload)

    monkeypatch.setattr(cache, "publish", capture)
    event_id = test_data["event"].id

    for _ in range(5):
        await leaderboard_service.schedule_push(event_id)
    await asyncio.sleep(0.5)

    assert len(sent) == 1
    assert sent[0]["type"] == "leaderboard" and sent[0]["event_id"] == event_id
    assert "entries" in sent[0]["data"]


@pytest.mark.asyncio
async def test_push_reaches_only_viewers_of_that_event():
    async with cache.subscription("evt_a") as viewer_a, cache.subscription("evt_b") as viewer_b:
        await cache.publish({"type": "leaderboard", "event_id": "evt_a", "data": {"entries": []}})
        frame = viewer_a.get_nowait()
        assert frame.startswith("data: ") and frame.endswith("\n\n") and '"evt_a"' in frame
        assert viewer_b.empty()
    assert cache.subscriber_count() == 0


@pytest.mark.asyncio
async def test_push_reaches_only_viewers_of_the_same_level():
    async with (
        cache.subscription("evt_c", level="smp-mts") as mts,
        cache.subscription("evt_c", level="sma-smk") as smk,
        cache.subscription("evt_c") as overall,
    ):
        await cache.publish(
            {"type": "leaderboard", "event_id": "evt_c", "level": "smp-mts", "data": {"entries": []}}
        )
        frame = mts.get_nowait()
        assert '"smp-mts"' in frame
        assert smk.empty() and overall.empty()
    assert cache.subscriber_count() == 0


def test_payment_status_polling_is_not_limited_as_invoice_creation():
    assert _bucket_name("POST", "/api/v1/payments") == "payment"
    assert _bucket_name("GET", "/api/v1/payments/pay_123/status") == "public"
