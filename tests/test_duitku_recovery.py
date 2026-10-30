"""Durable payment recovery with real database transactions and a simulated provider."""
import asyncio
import hashlib
import hmac
import time
import uuid
from unittest.mock import AsyncMock

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio

from app.core.config import settings
from app.core.database import async_session
from app.enums.payment_status import PaymentStatus
from app.repositories import payment as payment_repo, vote_log as vote_log_repo
from app.schemas.payment import PaymentCreate
from app.seeds.admin import seed_admin
from app.services import duitku_service, payment_service
from app.services.payment_service import PaymentError


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(settings, 'MIDTRANS_MOCK_MODE', False)
    monkeypatch.setattr(settings, 'DUITKU_ENVIRONMENT', 'sandbox')
    monkeypatch.setattr(settings, 'DUITKU_MERCHANT_CODE', 'test-merchant')
    monkeypatch.setattr(settings, 'DUITKU_API_KEY', 'test-only-key')
    inquiry = AsyncMock(side_effect=httpx.ReadTimeout('Provider response lost'))
    status = AsyncMock()
    monkeypatch.setattr(duitku_service, 'create_transaction', inquiry)
    monkeypatch.setattr(duitku_service, 'check_transaction_status', status)
    return inquiry, status


@pytest.fixture
def provider_ok(provider):
    """Inquiry berhasil: dipakai tes yang butuh invoice dengan tautan bayar."""
    inquiry, status = provider
    inquiry.side_effect = None
    inquiry.return_value = {'statusCode': '00', 'reference': 'test-reference',
                            'paymentUrl': 'https://sandbox.duitku.com/checkout/test'}
    return inquiry, status


def body(data):
    return {'event_id': data['event'].id, 'team_id': data['team'].id, 'qty': 3,
            'supporter_name': 'Recovery Test', 'supporter_phone': '081000000002',
            'supporter_email': 'test@example.com', 'idempotency_key': data['event'].id}


def other_request(data):
    """Body lain untuk phone+event yang sama, tanpa idempotency key.

    Request identik akan dijawab 'replay' sebelum guard dijalankan; body ini
    memastikan guard invoice aktif yang dievaluasi.
    """
    payload = body(data)
    payload.pop('idempotency_key')
    payload['supporter_email'] = 'other@example.com'
    return payload


async def latest_payment(data):
    """Baca ulang invoice terbaru dari DB dengan sesi baru (setelah commit request)."""
    async with async_session() as fresh:
        return (await payment_repo.list_payments(fresh, event_id=data['event'].id, limit=1))[0]


def status_response(code, **overrides):
    async def respond(*, merchant_order_id):
        return {'merchantOrderId': merchant_order_id, 'reference': 'test-reference',
                'amount': '3000', 'fee': '0.00', 'statusCode': code,
                'statusMessage': {'00': 'SUCCESS', '01': 'PROCESS', '02': 'CANCELED'}.get(code, 'UNKNOWN'), **overrides}
    return respond


def callback(payment):
    data = {'merchantCode': settings.DUITKU_MERCHANT_CODE, 'merchantOrderId': payment['id'],
            'amount': str(payment['amount']), 'reference': 'test-reference', 'resultCode': '00'}
    data['signature'] = hmac.new(settings.DUITKU_API_KEY.encode(),
        (data['merchantCode'] + data['amount'] + data['merchantOrderId']).encode(), hashlib.sha256).hexdigest()
    return data


@pytest.mark.asyncio
async def test_uncertain_inquiry_blocks_new_payment(async_client, test_data, provider):
    """Timeout = hasil tidak pasti: invoice PENDING dan memblokir order baru."""
    inquiry, _ = provider
    response = await async_client.post('/api/v1/payments', json=body(test_data))
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['status'] == 'PENDING'
    assert data['payment_url'] is None
    assert data['inquiry_state'] == 'unknown'

    retry = await async_client.post('/api/v1/payments', json=body(test_data))
    # Invoice lama dikembalikan apa adanya: PENDING unknown, tanpa tautan bayar.
    assert retry.status_code == 200, retry.text
    assert retry.json()['data']['id'] == data['id']
    assert retry.json()['data']['status'] == 'PENDING'
    assert retry.json()['data']['payment_url'] is None

    # Provider pulih, tetapi invoice lama belum diselesaikan.
    inquiry.side_effect = None
    inquiry.return_value = {'statusCode': '00', 'reference': 'test-reference',
                            'paymentUrl': 'https://sandbox.duitku.com/checkout/test'}
    still_replayed = await async_client.post('/api/v1/payments', json=body(test_data))
    assert still_replayed.status_code == 200, still_replayed.text
    assert still_replayed.json()['data']['id'] == data['id']
    assert inquiry.await_count == 1


@pytest.mark.asyncio
async def test_timeout_then_reconcile_settles_once(async_client, test_data, db_session, provider):
    inquiry, status = provider
    response = await async_client.post('/api/v1/payments', json=body(test_data))
    payment = response.json()['data']
    assert payment['status'] == 'PENDING'

    status.side_effect = status_response('00')
    reconciled = await async_client.post(_reconcile_url(payment))
    assert reconciled.json()['data']['status'] == 'SUCCESS'
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 3
    assert inquiry.await_count == 1

    confirmed = await async_client.post('/api/v1/payments/duitku/callback', json=callback(payment))
    assert confirmed.status_code == 200, confirmed.text
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 3


@pytest.mark.asyncio
async def test_duitku_pending_past_expiry_still_blocks(async_client, test_data, db_session, provider_ok):
    payment = (await async_client.post('/api/v1/payments', json=body(test_data))).json()['data']
    stored = await payment_repo.get_payment(db_session, payment['id'], for_update=True)
    stored.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db_session.commit()

    # Request lain (wajah berbeda) harus tetap diblokir guard meski invoice
    # sudah lewat expires_at: expiry lokal tidak menyentuh invoice Duitku.
    with pytest.raises(PaymentError):
        await payment_service.create_payment(db_session, PaymentCreate(**other_request(test_data)))
    db_session.expire_all()
    still = await payment_repo.get_payment(db_session, payment['id'])
    assert still.status == PaymentStatus.PENDING


@pytest.mark.asyncio
async def test_creating_state_blocks_new_invoice(async_client, test_data, db_session, provider_ok):
    payment = (await async_client.post('/api/v1/payments', json=body(test_data))).json()['data']
    stored = await payment_repo.get_payment(db_session, payment['id'], for_update=True)
    stored.duitku_payload = {**stored.duitku_payload, '_inquiry_state': 'creating'}
    await db_session.commit()

    with pytest.raises(PaymentError):
        await payment_service.create_payment(db_session, PaymentCreate(**other_request(test_data)))


@pytest.mark.asyncio
async def test_rejected_invoice_releases_key_and_allows_retry(async_client, test_data, db_session, provider_ok):
    inquiry, _ = provider_ok
    inquiry.side_effect = [duitku_service.DuitkuRejected('Payment method is inactive', '01')]
    rejected = await async_client.post('/api/v1/payments', json=body(test_data))
    assert rejected.status_code == 400, rejected.text
    failed = await latest_payment(test_data)
    assert failed.status == PaymentStatus.FAILED
    assert failed.idempotency_key is None

    inquiry.side_effect = None
    retry = await async_client.post('/api/v1/payments', json=body(test_data))
    assert retry.status_code == 200, retry.text
    assert inquiry.await_count == 2


@pytest.mark.asyncio
async def test_reconcile_does_not_revive_canceled_with_reference(async_client, test_data, db_session, provider_ok):
    _, status = provider_ok
    payment = (await async_client.post('/api/v1/payments', json=body(test_data))).json()['data']
    stored = await payment_repo.get_payment(db_session, payment['id'], for_update=True)
    stored.status = PaymentStatus.CANCELED
    stored.duitku_reference = 'test-reference'
    await db_session.commit()
    status.reset_mock()

    response = await async_client.post(_reconcile_url(payment))
    assert response.status_code == 200, response.text
    assert response.json()['data']['status'] == 'CANCELED'
    status.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_invoice_can_still_settle(async_client, test_data, db_session, provider_ok):
    _, status = provider_ok
    payment = (await async_client.post('/api/v1/payments', json=body(test_data))).json()['data']
    stored = await payment_repo.get_payment(db_session, payment['id'], for_update=True)
    stored.status = PaymentStatus.EXPIRED
    assert stored.duitku_reference == 'test-reference'
    await db_session.commit()
    status.side_effect = status_response('00')

    response = await async_client.post(_reconcile_url(payment))
    assert response.json()['data']['status'] == 'SUCCESS'
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 3


@pytest.mark.asyncio
async def test_official_status_is_reconciled_on_confirmed_invoice(async_client, test_data, db_session, provider_ok):
    inquiry, status = provider_ok
    payment = (await async_client.post('/api/v1/payments', json=body(test_data))).json()['data']
    assert payment['status'] == 'PENDING'
    assert payment['payment_url']
    status.side_effect = status_response('00')
    response = await async_client.post(_reconcile_url(payment))
    assert response.json()['data']['status'] == 'SUCCESS'
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('overrides', [{'amount': '1'}, {'merchantOrderId': 'other-order'}, {'reference': ''}, {'statusCode': 'bogus'}])
async def test_untrusted_status_cannot_credit_votes(async_client, test_data, db_session, provider_ok, overrides):
    _, status = provider_ok
    status.side_effect = status_response('00', **overrides)
    payment = (await async_client.post('/api/v1/payments', json=body(test_data))).json()['data']
    assert payment['status'] == 'PENDING'
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 0


@pytest.mark.asyncio
async def test_callback_can_arrive_before_inquiry_returns(async_client, test_data, db_session, provider):
    inquiry, status = provider
    status.side_effect = status_response('00')

    async def respond(**kwargs):
        order_id = kwargs['merchant_order_id']
        async with async_session() as other:
            saved = await payment_repo.get_by_duitku_merchant_order_id(other, order_id)
            assert saved is not None, 'Invoice must be committed before contacting provider'
        delivered = await async_client.post('/api/v1/payments/duitku/callback', data=callback({'id': order_id, 'amount': 3000}))
        assert delivered.status_code == 200, delivered.text
        return {'statusCode': '00', 'reference': 'test-reference',
                'paymentUrl': 'https://sandbox.duitku.com/checkout/test', 'qrString': 'test-qr'}

    inquiry.side_effect = respond
    payment = (await async_client.post('/api/v1/payments', json=body(test_data))).json()['data']
    assert payment['status'] == 'SUCCESS'
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 3


@pytest.mark.asyncio
async def test_parallel_retries_issue_only_one_provider_order(async_client, test_data, provider_ok):
    inquiry, status = provider_ok
    status.side_effect = status_response('01')
    replies = await asyncio.gather(*(async_client.post('/api/v1/payments', json=body(test_data)) for _ in range(4)))
    assert all(r.status_code == 200 for r in replies), [r.text for r in replies]
    assert len({r.json()['data']['id'] for r in replies}) == 1
    assert inquiry.await_count == 1


@pytest.mark.asyncio
async def test_parallel_manual_checks_and_callback_credit_once(async_client, test_data, db_session, provider):
    inquiry, status = provider
    inquiry.side_effect = None
    inquiry.return_value = {'statusCode': '00', 'reference': 'test-reference',
                            'paymentUrl': 'https://sandbox.duitku.com/checkout/test'}
    payment = (await async_client.post('/api/v1/payments', json=body(test_data))).json()['data']
    status.side_effect = status_response('00')
    replies = await asyncio.gather(
        *(async_client.post(_reconcile_url(payment)) for _ in range(4)),
        async_client.post('/api/v1/payments/duitku/callback', data=callback(payment)),
    )
    assert all(r.status_code == 200 for r in replies), [r.text for r in replies]
    assert status.await_count <= 1
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 3


@pytest.mark.asyncio
async def test_status_reference_mismatch_cannot_credit_votes(async_client, test_data, db_session, provider):
    inquiry, status = provider
    inquiry.side_effect = None
    inquiry.return_value = {'statusCode': '00', 'reference': 'test-reference',
                            'paymentUrl': 'https://sandbox.duitku.com/checkout/test'}
    payment = (await async_client.post('/api/v1/payments', json=body(test_data))).json()['data']
    status.side_effect = status_response('00', reference='different-reference')
    response = await async_client.post(_reconcile_url(payment))
    assert response.status_code == 400
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 0


@pytest.mark.asyncio
async def test_reconciliation_cannot_use_credentials_from_another_environment(async_client, test_data, provider_ok, monkeypatch):
    _, status = provider_ok
    status.side_effect = status_response('01')
    payment = (await async_client.post('/api/v1/payments', json=body(test_data))).json()['data']
    status.reset_mock()
    monkeypatch.setattr(settings, 'DUITKU_ENVIRONMENT', 'production')
    response = await async_client.post(_reconcile_url(payment))
    assert response.status_code == 503
    status.assert_not_awaited()


# --- Plan 02: verdict, backoff, lease, dan manual review ---------------------

@pytest_asyncio.fixture
async def admin_headers(async_client):
    email = f"admin-{uuid.uuid4().hex[:8]}@test.local"
    password = uuid.uuid4().hex
    await seed_admin(email, password)
    login = await async_client.post(
        "/api/v1/auth/admin/login", json={"email": email, "password": password}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['data']['access_token']}"}


def _reconcile_url(payment):
    token = payment_service.status_token(payment['id'])
    return f"/api/v1/payments/{payment['id']}/reconcile?t={token}"


async def _set_payload(db_session, payment_id, **fields):
    stored = await payment_repo.get_payment(db_session, payment_id, for_update=True)
    stored.duitku_payload = {**(stored.duitku_payload or {}), **fields}
    await db_session.commit()


async def _new_unknown_invoice(async_client, test_data):
    """Invoice hasil inquiry timeout: PENDING, unknown, tanpa tautan bayar."""
    return (await async_client.post('/api/v1/payments', json=body(test_data))).json()['data']


@pytest.mark.asyncio
async def test_verdict_present_on_success(async_client, test_data, db_session, provider):
    _, status = provider
    payment = await _new_unknown_invoice(async_client, test_data)
    status.side_effect = status_response('00')

    response = await async_client.post(_reconcile_url(payment))

    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['reconcile_verdict'] == 'PRESENT'
    assert data['status'] == 'SUCCESS'
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 3


@pytest.mark.asyncio
async def test_verdict_present_on_pending(async_client, test_data, provider):
    _, status = provider
    payment = await _new_unknown_invoice(async_client, test_data)
    status.side_effect = status_response('01')

    response = await async_client.post(_reconcile_url(payment))

    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['reconcile_verdict'] == 'PRESENT'
    assert data['status'] == 'PENDING'


@pytest.mark.asyncio
async def test_verdict_absent_on_canceled(async_client, test_data, provider):
    _, status = provider
    payment = await _new_unknown_invoice(async_client, test_data)
    status.side_effect = status_response('02')

    response = await async_client.post(_reconcile_url(payment))

    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['reconcile_verdict'] == 'ABSENT_CONFIRMED'
    assert data['status'] == 'CANCELED'


@pytest.mark.asyncio
async def test_verdict_indeterminate_on_timeout(async_client, test_data, db_session, provider):
    _, status = provider
    payment = await _new_unknown_invoice(async_client, test_data)
    status.side_effect = httpx.ReadTimeout('Provider response lost')

    response = await async_client.post(_reconcile_url(payment))

    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['reconcile_verdict'] == 'INDETERMINATE'
    assert data['status'] == 'PENDING'
    stored = await payment_repo.get_payment(db_session, payment['id'])
    assert stored.duitku_payload['_attempts'] == 1
    assert stored.duitku_payload['_last_verdict'] == 'INDETERMINATE'


@pytest.mark.asyncio
async def test_verdict_indeterminate_on_unknown_status(async_client, test_data, db_session, provider):
    _, status = provider
    payment = await _new_unknown_invoice(async_client, test_data)
    # G0.5 belum terjawab: "tidak ditemukan" yang tak bisa dibedakan dari
    # propagasi tetap INDETERMINATE, bukan ABSENT_CONFIRMED.
    status.side_effect = status_response('99')

    response = await async_client.post(_reconcile_url(payment))

    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['reconcile_verdict'] == 'INDETERMINATE'
    assert data['status'] == 'PENDING'
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 0


def test_backoff_grows_and_caps():
    assert [payment_service.reconcile_delay(n) for n in range(1, 7)] == [60, 120, 240, 480, 900, 900]


@pytest.mark.asyncio
async def test_manual_review_after_max_attempts(async_client, test_data, db_session, provider):
    _, status = provider
    payment = await _new_unknown_invoice(async_client, test_data)
    await _set_payload(db_session, payment['id'], _attempts=9)
    status.side_effect = httpx.ReadTimeout('Provider response lost')

    response = await async_client.post(_reconcile_url(payment))

    assert response.status_code == 200, response.text
    assert response.json()['data']['needs_manual_review'] is True
    stored = await payment_repo.get_payment(db_session, payment['id'])
    assert stored.status == PaymentStatus.PENDING
    assert stored.duitku_payload['_attempts'] == 10

    # Tetap memblokir pembuatan invoice baru (D13). Body identik akan dijawab
    # replay sebelum guard dievaluasi, jadi pakai request lain untuk phone+event.
    blocked = await async_client.post('/api/v1/payments', json=other_request(test_data))
    assert blocked.status_code == 400, blocked.text
    # API memakai envelope {success, message, data} (lihat app/main.py).
    assert 'masih kami proses' in blocked.json()['message']


@pytest.mark.asyncio
async def test_lease_prevents_parallel_provider_calls(async_client, test_data, db_session, provider):
    _, status = provider
    payment = await _new_unknown_invoice(async_client, test_data)
    status.side_effect = status_response('01')
    # Lease milik proses lain masih aktif: tidak boleh memanggil provider,
    # dan tombol manual tidak ditolak (bukan throttle lagi).
    await _set_payload(db_session, payment['id'], _lease_owner='another-worker',
                       _lease_until=time.time() + 60)

    response = await async_client.post(_reconcile_url(payment))

    assert response.status_code == 200, response.text
    assert response.json()['data']['reconcile_verdict'] == 'INDETERMINATE'
    # Lease milik worker lain masih aktif: provider tidak boleh dipanggil sama sekali.
    assert status.await_count == 0


@pytest.mark.asyncio
async def test_expired_lease_can_be_taken_over(async_client, test_data, db_session, provider):
    _, status = provider
    payment = await _new_unknown_invoice(async_client, test_data)
    status.side_effect = status_response('01')
    await _set_payload(db_session, payment['id'], _lease_owner='another-worker',
                       _lease_until=time.time() - 1)

    response = await async_client.post(_reconcile_url(payment))

    assert response.status_code == 200, response.text
    assert response.json()['data']['reconcile_verdict'] == 'PRESENT'
    # Lease kedaluwarsa boleh diambil alih: provider dipanggil sekali oleh reconcile.
    assert status.await_count == 1
    stored = await payment_repo.get_payment(db_session, payment['id'])
    assert '_lease_until' not in stored.duitku_payload


@pytest.mark.asyncio
async def test_admin_confirm_paid_is_idempotent(async_client, test_data, db_session, admin_headers):
    payment = await _new_unknown_invoice(async_client, test_data)
    stored = await payment_repo.get_payment(db_session, payment['id'], for_update=True)
    stored.duitku_reference = 'test-reference'
    await db_session.commit()

    payload = {'action': 'confirm_paid', 'reason': 'bukti transfer diverifikasi'}
    first = await async_client.post(f"/api/v1/admin/payments/{payment['id']}/resolve",
                                    json=payload, headers=admin_headers)
    assert first.status_code == 200, first.text
    second = await async_client.post(f"/api/v1/admin/payments/{payment['id']}/resolve",
                                     json=payload, headers=admin_headers)
    assert second.status_code == 200, second.text

    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 3
    async with async_session() as fresh:
        stored = await payment_repo.get_payment(fresh, payment['id'])
        assert stored.status == PaymentStatus.SUCCESS
        assert stored.duitku_payload['_manual_resolution']['action'] == 'confirm_paid'


@pytest.mark.asyncio
async def test_admin_cancel_unpaid_requires_no_reference_or_absent_verdict(async_client, test_data, db_session, admin_headers):
    payment = await _new_unknown_invoice(async_client, test_data)
    stored = await payment_repo.get_payment(db_session, payment['id'], for_update=True)
    stored.duitku_reference = 'test-reference'
    await db_session.commit()

    payload = {'action': 'cancel_unpaid', 'reason': 'pelanggan tidak membayar'}
    rejected = await async_client.post(f"/api/v1/admin/payments/{payment['id']}/resolve",
                                       json=payload, headers=admin_headers)
    assert rejected.status_code == 400, rejected.text

    # Provider memastikan order tidak bisa dibayar -> baru boleh dibatalkan.
    await _set_payload(db_session, payment['id'], _last_verdict='ABSENT_CONFIRMED')
    accepted = await async_client.post(f"/api/v1/admin/payments/{payment['id']}/resolve",
                                       json=payload, headers=admin_headers)
    assert accepted.status_code == 200, accepted.text
    async with async_session() as fresh:
        stored = await payment_repo.get_payment(fresh, payment['id'])
        assert stored.status == PaymentStatus.CANCELED
        assert stored.idempotency_key is None
    assert stored.duitku_payload['_inquiry_state'] == 'rejected'
