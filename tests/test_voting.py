"""
Tests for the voting and payment workflow.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock
import httpx

from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.services import duitku_service, payment_service

# Fixtures (db_session, async_client, test_data) live in conftest.py.


@pytest.mark.asyncio
async def test_create_payment_and_vote(async_client: AsyncClient, db_session: AsyncSession, test_data: dict):
    """
    Tests the full vote flow:
    1. Create a payment (invoice).
    2. Simulate a successful payment webhook.
    3. Verify that the vote is logged correctly.
    """
    event = test_data["event"]
    team = test_data["team"]
    vote_qty = 5

    # 1. Create a payment
    create_payload = {
        "event_id": event.id,
        "team_id": team.id,
        "qty": vote_qty,
        "supporter_name": "Test Voter",
        "supporter_phone": "1234567890",
    }
    response = await async_client.post("/api/v1/payments", json=create_payload)
    assert response.status_code == 200
    payment_data = response.json()["data"]
    assert payment_data["status"] == "PENDING"
    assert payment_data["votes"] == vote_qty
    assert payment_data["amount"] == event.price_per_vote * vote_qty

    payment_id = payment_data["id"]

    # 2. Simulate a successful webhook callback from Midtrans
    # In a real scenario, we'd mock the webhook signature verification
    # In explicit mock mode (no server key) the signature is not checked.
    webhook_payload = {
        "order_id": payment_id,
        "transaction_status": "settlement",
        "status_code": "200",
        "gross_amount": str(float(payment_data["amount"])),
    }

    # Explicit mock mode (conftest) accepts unsigned notifications.
    webhook_response = await async_client.post("/api/v1/payments/webhook", json=webhook_payload)

    assert webhook_response.status_code == 200
    assert webhook_response.json()["data"]["status"] == "SUCCESS"

    # 3. Verify vote log and payment status in the database
    from app.repositories import payment as payment_repo
    from app.repositories import vote_log as vote_log_repo

    db_payment = await payment_repo.get_payment(db_session, payment_id)
    assert db_payment is not None
    assert db_payment.status == payment_service.PaymentStatus.SUCCESS

    # Check that the votes were correctly logged for the team
    team_votes = await vote_log_repo.get_vote_count_for_team(db_session, team.id)
    assert team_votes == vote_qty


@pytest.mark.asyncio
async def test_failed_payment_does_not_add_vote(async_client: AsyncClient, db_session: AsyncSession, test_data: dict):
    """
    Tests that a failed payment webhook does not grant any votes.
    1. Create a payment (invoice).
    2. Simulate a failed payment webhook ('deny').
    3. Verify that the payment status is FAILED and no vote is logged.
    """
    event = test_data["event"]
    team = test_data["team"]
    vote_qty = 10

    # 1. Create a payment
    create_payload = {
        "event_id": event.id,
        "team_id": team.id,
        "qty": vote_qty,
        "supporter_name": "Failing Voter",
        "supporter_phone": "0987654321",
    }
    response = await async_client.post("/api/v1/payments", json=create_payload)
    assert response.status_code == 200
    payment_data = response.json()["data"]
    payment_id = payment_data["id"]

    # 2. Simulate a failed webhook callback
    webhook_payload = {
        "order_id": payment_id,
        "transaction_status": "deny",  # Failed status
        "status_code": "201",
        "gross_amount": str(float(payment_data["amount"])),
    }

    webhook_response = await async_client.post("/api/v1/payments/webhook", json=webhook_payload)

    assert webhook_response.status_code == 200
    # The webhook processor itself doesn't return failure, just the processed status
    assert webhook_response.json()["data"]["status"] == "FAILED"

    # 3. Verify vote log and payment status in the database
    from app.repositories import payment as payment_repo
    from app.repositories import vote_log as vote_log_repo

    db_payment = await payment_repo.get_payment(db_session, payment_id)
    assert db_payment is not None
    assert db_payment.status == payment_service.PaymentStatus.FAILED

    # CRITICAL: Ensure no votes were logged for this team
    team_votes = await vote_log_repo.get_vote_count_for_team(db_session, team.id)
    assert team_votes == 0


@pytest.fixture
def duitku_guard(monkeypatch):
    """Midtrans legacy disabled; Duitku create_transaction returns an unknown
    inquiry_state so the duplicate guard is exercised on the real path."""
    monkeypatch.setattr(settings, 'MIDTRANS_MOCK_MODE', False)
    monkeypatch.setattr(settings, 'DUITKU_MERCHANT_CODE', 'test-merchant')
    monkeypatch.setattr(settings, 'DUITKU_API_KEY', 'test-only-key')
    gateway = AsyncMock(return_value={
        'statusCode': '00', 'reference': 'test-reference',
        'paymentUrl': 'https://passport.duitku.com/checkout/test', 'qrString': 'test-qr',
    })
    monkeypatch.setattr(duitku_service, 'create_transaction', gateway)
    monkeypatch.setattr(duitku_service, 'check_transaction_status', AsyncMock(side_effect=httpx.ReadTimeout('Status unavailable')))
    return gateway


@pytest.mark.asyncio
async def test_duplicate_pending_returns_400(async_client: AsyncClient, db_session: AsyncSession, test_data: dict, duitku_guard):
    """
    A second invoice with the same phone+event must be rejected with 400.
    The payment gateway is a third party: when it only reports a result, the
    system cannot track or override its status, so the guard must hold the
    order open instead of letting a duplicate through.
    """
    event = test_data["event"]
    team = test_data["team"]

    create_payload = {
        "event_id": event.id,
        "team_id": team.id,
        "qty": 1,
        "supporter_name": "Duplicate Voter",
        "supporter_phone": "081234567890",
        "supporter_email": "sandbox-test@example.com",
    }

    # First creation should succeed
    response1 = await async_client.post("/api/v1/payments", json=create_payload)
    assert response1.status_code == 200
    payment_data_1 = response1.json()["data"]
    assert payment_data_1["status"] == "PENDING"

    # Second creation with same phone+event should fail with 400
    response2 = await async_client.post("/api/v1/payments", json=create_payload)
    assert response2.status_code == 400
    assert "masih kami proses" in response2.json()["message"]
    assert "Jangan membuat pembayaran baru" in response2.json()["message"]

    # Verify only one PENDING payment exists for this phone+event
    from app.repositories import payment as payment_repo
    from app.enums.payment_status import PaymentStatus

    pendings = await payment_repo.list_payments(
        db_session, event_id=event.id, status=PaymentStatus.PENDING
    )
    phone_pendings = [p for p in pendings if p.supporter_phone == create_payload["supporter_phone"]]
    assert len(phone_pendings) == 1


@pytest.mark.asyncio
async def test_expired_pending_allows_new_payment(async_client: AsyncClient, db_session: AsyncSession, test_data: dict):
    """
    Tests that after a PENDING invoice expires, a new payment can be created.
    """
    event = test_data["event"]
    team = test_data["team"]

    create_payload = {
        "event_id": event.id,
        "team_id": team.id,
        "qty": 1,
        "supporter_name": "Expired Voter",
        "supporter_phone": "082345678901",
    }

    # First creation
    response1 = await async_client.post("/api/v1/payments", json=create_payload)
    assert response1.status_code == 200
    payment_data_1 = response1.json()["data"]
    payment_id_1 = payment_data_1["id"]
    assert payment_data_1["status"] == "PENDING"

    # Manually expire the payment by setting expires_at to the past
    from app.repositories import payment as payment_repo
    from app.enums.payment_status import PaymentStatus
    from datetime import datetime, timezone

    payment = await payment_repo.get_payment(db_session, payment_id_1)
    assert payment is not None
    payment.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db_session.commit()

    # Second creation should succeed because the first one is expired
    response2 = await async_client.post("/api/v1/payments", json=create_payload)
    assert response2.status_code == 200
    payment_data_2 = response2.json()["data"]
    assert payment_data_2["status"] == "PENDING"
    assert payment_data_2["id"] != payment_id_1

    # Verify the first payment is now EXPIRED
    await db_session.refresh(payment)
    assert payment.status == PaymentStatus.EXPIRED


@pytest.mark.asyncio
async def test_success_payment_allows_new_payment(async_client: AsyncClient, db_session: AsyncSession, test_data: dict):
    """
    Tests that after a payment succeeds, a new payment can be created.
    """
    event = test_data["event"]
    team = test_data["team"]

    create_payload = {
        "event_id": event.id,
        "team_id": team.id,
        "qty": 1,
        "supporter_name": "Success Voter",
        "supporter_phone": "083456789012",
    }

    # First creation
    response1 = await async_client.post("/api/v1/payments", json=create_payload)
    assert response1.status_code == 200
    payment_data_1 = response1.json()["data"]
    payment_id_1 = payment_data_1["id"]

    # Simulate successful webhook
    webhook_payload = {
        "order_id": payment_id_1,
        "transaction_status": "settlement",
        "status_code": "200",
        "gross_amount": str(float(payment_data_1["amount"])),
    }

    webhook_response = await async_client.post("/api/v1/payments/webhook", json=webhook_payload)

    assert webhook_response.status_code == 200
    assert webhook_response.json()["data"]["status"] == "SUCCESS"

    # Second creation should succeed because the first one is SUCCESS
    response2 = await async_client.post("/api/v1/payments", json=create_payload)
    assert response2.status_code == 200
    payment_data_2 = response2.json()["data"]
    assert payment_data_2["status"] == "PENDING"
    assert payment_data_2["id"] != payment_id_1


@pytest.mark.asyncio
async def test_different_phone_allows_new_payment(async_client: AsyncClient, db_session: AsyncSession, test_data: dict):
    """
    Tests that a different phone number can create a new PENDING payment
    even if another phone has an active PENDING for the same event.
    """
    event = test_data["event"]
    team = test_data["team"]

    create_payload_1 = {
        "event_id": event.id,
        "team_id": team.id,
        "qty": 1,
        "supporter_name": "Phone A",
        "supporter_phone": "084567890123",
    }

    create_payload_2 = {
        "event_id": event.id,
        "team_id": team.id,
        "qty": 1,
        "supporter_name": "Phone B",
        "supporter_phone": "085678901234",
    }

    # Both should succeed because phones are different
    response1 = await async_client.post("/api/v1/payments", json=create_payload_1)
    assert response1.status_code == 200

    response2 = await async_client.post("/api/v1/payments", json=create_payload_2)
    assert response2.status_code == 200

    # Verify two different PENDING payments exist
    from app.repositories import payment as payment_repo
    from app.enums.payment_status import PaymentStatus

    pendings = await payment_repo.list_payments(
        db_session, event_id=event.id, status=PaymentStatus.PENDING
    )
    assert len(pendings) == 2
