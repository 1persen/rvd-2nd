"""Durable payment recovery with real database transactions and a simulated provider."""
import asyncio
import hashlib
import hmac
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import settings
from app.core.database import async_session
from app.repositories import payment as payment_repo, vote_log as vote_log_repo
from app.services import duitku_service


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(settings, 'MIDTRANS_MOCK_MODE', False)
    monkeypatch.setattr(settings, 'DUITKU_ENVIRONMENT', 'sandbox')
    monkeypatch.setattr(settings, 'DUITKU_PAYMENT_METHOD', 'NQ')
    monkeypatch.setattr(settings, 'DUITKU_MERCHANT_CODE', 'test-merchant')
    monkeypatch.setattr(settings, 'DUITKU_API_KEY', 'test-only-key')
    inquiry = AsyncMock(side_effect=httpx.ReadTimeout('Provider response lost'))
    status = AsyncMock()
    monkeypatch.setattr(duitku_service, 'create_transaction', inquiry)
    monkeypatch.setattr(duitku_service, 'check_transaction_status', status)
    monkeypatch.setattr(duitku_service, 'get_payment_methods', AsyncMock(return_value=[
        {'paymentMethod': 'NQ', 'paymentName': 'NOBU QRIS', 'paymentImage': '', 'totalFee': '0'},
    ]))
    return inquiry, status


def body(data):
    return {'event_id': data['event'].id, 'team_id': data['team'].id, 'qty': 3,
            'supporter_name': 'Recovery Test', 'supporter_phone': '081000000002',
            'supporter_email': 'test@example.com', 'idempotency_key': data['event'].id}


def status_response(code, **overrides):
    async def respond(*, merchant_order_id):
        return {'merchantOrderId': merchant_order_id, 'reference': 'test-reference',
                'amount': '3000', 'fee': '0.00', 'statusCode': code,
                'statusMessage': {'00': 'SUCCESS', '01': 'PROCESS', '02': 'CANCELED'}[code], **overrides}
    return respond


def callback(payment):
    data = {'merchantCode': settings.DUITKU_MERCHANT_CODE, 'merchantOrderId': payment['id'],
            'amount': str(payment['amount']), 'reference': 'test-reference', 'resultCode': '00'}
    data['signature'] = hmac.new(settings.DUITKU_API_KEY.encode(),
        (data['merchantCode'] + data['amount'] + data['merchantOrderId']).encode(), hashlib.sha256).hexdigest()
    return data


@pytest.mark.asyncio
@pytest.mark.parametrize('code,expected,votes', [('01', 'PENDING', 0), ('00', 'SUCCESS', 3), ('02', 'CANCELED', 0)])
async def test_official_status_without_payment_url_recovers_invoice(async_client, test_data, db_session, provider, code, expected, votes):
    inquiry, status = provider
    status.side_effect = status_response(code)
    response = await async_client.post('/api/v1/payments', json=body(test_data))
    assert response.status_code == 200, response.text
    payment = response.json()['data']
    assert payment['status'] == expected
    assert payment['is_sandbox']
    assert payment['payment_url'] is None  # Never fabricate a checkout URL.
    retry = await async_client.post('/api/v1/payments', json=body(test_data))
    assert retry.json()['data']['id'] == payment['id']
    assert inquiry.await_count == 1
    assert await payment_repo.get_payment(db_session, payment['id']) is not None
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == votes
    if code in ('00', '01'):
        for _ in range(2):
            delivered = await async_client.post('/api/v1/payments/duitku/callback', data=callback(payment))
            assert delivered.status_code == 200, delivered.text
        assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 3


@pytest.mark.asyncio
async def test_unknown_result_remains_durable_and_manual_checks_are_throttled(async_client, test_data, db_session, provider):
    inquiry, status = provider
    status.side_effect = httpx.ReadTimeout('Status unavailable too')
    payment = (await async_client.post('/api/v1/payments', json=body(test_data))).json()['data']
    assert payment['status'] == 'PENDING'
    assert payment['message']
    assert payment['is_sandbox']
    # Ordinary browser polling must never poll Duitku.
    for _ in range(3):
        assert (await async_client.get(f"/api/v1/payments/{payment['id']}/status")).status_code == 200
        assert (await async_client.post(f"/api/v1/payments/{payment['id']}/reconcile")).status_code == 200
    assert status.await_count == 1
    saved = await payment_repo.get_payment(db_session, payment['id'])
    saved.duitku_payload = {**saved.duitku_payload, '_status_checked_at': 0}
    await db_session.commit()
    status.side_effect = status_response('00')
    response = await async_client.post(f"/api/v1/payments/{payment['id']}/reconcile")
    assert response.json()['data']['status'] == 'SUCCESS'
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('overrides', [{'amount': '1'}, {'merchantOrderId': 'other-order'}, {'reference': ''}, {'statusCode': 'bogus'}])
async def test_untrusted_status_cannot_credit_votes(async_client, test_data, db_session, provider, overrides):
    _, status = provider
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
async def test_parallel_retries_issue_only_one_provider_order(async_client, test_data, provider):
    inquiry, status = provider
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
        *(async_client.post(f"/api/v1/payments/{payment['id']}/reconcile") for _ in range(4)),
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
    response = await async_client.post(f"/api/v1/payments/{payment['id']}/reconcile")
    assert response.status_code == 400
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 0


@pytest.mark.asyncio
async def test_reconciliation_cannot_use_credentials_from_another_environment(async_client, test_data, provider, monkeypatch):
    _, status = provider
    status.side_effect = status_response('01')
    payment = (await async_client.post('/api/v1/payments', json=body(test_data))).json()['data']
    status.reset_mock()
    monkeypatch.setattr(settings, 'DUITKU_ENVIRONMENT', 'production')
    response = await async_client.post(f"/api/v1/payments/{payment['id']}/reconcile")
    assert response.status_code == 503
    status.assert_not_awaited()
