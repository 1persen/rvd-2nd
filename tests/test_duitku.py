"""Duitku checkout and callback integrity against the disposable database."""
import hashlib
import hmac
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.repositories import payment as payment_repo, vote_log as vote_log_repo
from app.services import duitku_service


@pytest.fixture
def duitku(monkeypatch):
    monkeypatch.setattr(settings, 'MIDTRANS_MOCK_MODE', False)
    monkeypatch.setattr(settings, 'DUITKU_MERCHANT_CODE', 'test-merchant')
    monkeypatch.setattr(settings, 'DUITKU_API_KEY', 'test-only-key')
    gateway = AsyncMock(return_value={
        'statusCode': '00', 'reference': 'test-reference',
        'paymentUrl': 'https://passport.duitku.com/checkout/test', 'qrString': 'test-qr',
    })
    monkeypatch.setattr(duitku_service, 'create_transaction', gateway)
    return gateway


async def invoice(client, data):
    return await client.post('/api/v1/payments', json={
        'event_id': data['event'].id, 'team_id': data['team'].id, 'qty': 3,
        'supporter_name': 'Test', 'supporter_phone': '08123456789',
    })


def callback(payment, **overrides):
    payload = {'merchantCode': settings.DUITKU_MERCHANT_CODE,
               'merchantOrderId': payment['id'], 'amount': str(payment['amount']),
               'resultCode': '00', 'reference': 'test-reference'}
    payload.update(overrides)
    message = payload['merchantCode'] + payload['amount'] + payload['merchantOrderId']
    payload['signature'] = hmac.new(settings.DUITKU_API_KEY.encode(), message.encode(), hashlib.sha256).hexdigest()
    return payload


@pytest.mark.asyncio
async def test_checkout_uses_duitku_without_midtrans_key(async_client, test_data, duitku):
    response = await invoice(async_client, test_data)
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['payment_gateway'] == 'DUITKU'
    assert not data['is_mock']
    assert data['payment_url'] == duitku.return_value['paymentUrl']
    assert data['qr_string'] == 'test-qr'
    assert duitku.call_args.kwargs['payment_method'] == 'SP'


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [{'statusCode': '01'}, {'statusCode': '00', 'reference': 'r', 'paymentUrl': 'javascript:alert(1)'}])
async def test_rejected_inquiry_does_not_persist_invoice(async_client, test_data, duitku, bad):
    duitku.return_value = bad
    response = await invoice(async_client, test_data)
    assert response.status_code == 400
    # Retry can create an invoice; the rejected attempt left no pending row.
    duitku.return_value = {'statusCode': '00', 'reference': 'retry', 'paymentUrl': 'https://passport.duitku.com/checkout/test'}
    retry = await invoice(async_client, test_data)
    assert retry.status_code == 200, retry.text


@pytest.mark.asyncio
async def test_forged_duitku_cannot_bypass_midtrans_signature(async_client, monkeypatch):
    monkeypatch.setattr(settings, 'MIDTRANS_MOCK_MODE', False)
    response = await async_client.post('/api/v1/payments/webhook', json={
        'merchantOrderId': 'nonexistent', 'resultCode': '00',
    })
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unconfigured_callback_fails_closed(async_client, monkeypatch):
    monkeypatch.setattr(settings, 'DUITKU_MERCHANT_CODE', '')
    monkeypatch.setattr(settings, 'DUITKU_API_KEY', '')
    response = await async_client.post('/api/v1/payments/duitku/callback', data={
        'merchantOrderId': 'nonexistent', 'resultCode': '00',
    })
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['signature', 'merchant', 'amount', 'reference', 'result'])
async def test_invalid_callback_never_grants_votes(async_client, test_data, db_session, duitku, invalid):
    payment = (await invoice(async_client, test_data)).json()['data']
    changes = {'merchant': {'merchantCode': 'other'}, 'amount': {'amount': '1'},
               'reference': {'reference': 'other'}, 'result': {'resultCode': 'garbage'}}
    payload = callback(payment, **changes.get(invalid, {}))
    if invalid == 'signature':
        payload['signature'] = '0' * 64
    response = await async_client.post('/api/v1/payments/duitku/callback', data=payload)
    assert response.status_code in (400, 403)
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 0
    persisted = await payment_repo.get_payment(db_session, payment['id'])
    assert persisted.status.value == 'PENDING'


@pytest.mark.asyncio
async def test_valid_callback_is_idempotent(async_client, test_data, db_session, duitku):
    payment = (await invoice(async_client, test_data)).json()['data']
    for _ in range(2):
        response = await async_client.post('/api/v1/payments/duitku/callback', data=callback(payment))
        assert response.status_code == 200, response.text
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 3


@pytest.mark.asyncio
async def test_checkout_page_accepts_duitku_credentials(async_client, test_data, duitku):
    response = await async_client.get(f"/events/{test_data['event'].id}/vote/{test_data['team'].id}")
    assert response.status_code == 200
    assert 'id="voter-phone"' in response.text
    assert 'Buka pembayaran Duitku' in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [[], None, {'signature': 123}, {
    'merchantCode': 'test-merchant', 'merchantOrderId': 'id', 'amount': '1000', 'signature': 'é' * 64,
}])
async def test_malformed_callbacks_are_rejected(async_client, duitku, payload):
    response = await async_client.post('/api/v1/payments/duitku/callback', json=payload)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_parallel_duitku_callbacks_grant_votes_once(async_client, test_data, db_session, duitku):
    import asyncio
    from app.core.database import async_session
    from app.services.payment_service import process_duitku_callback

    payment = (await invoice(async_client, test_data)).json()['data']
    payload = callback(payment)

    async def settle():
        async with async_session() as session:
            _, granted = await process_duitku_callback(session, payload)
            await session.commit()
            return granted

    outcomes = await asyncio.gather(*(settle() for _ in range(5)))
    assert outcomes.count(True) == 1
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 3


@pytest.mark.asyncio
async def test_duplicate_callback_fields_are_rejected(async_client, duitku):
    response = await async_client.post('/api/v1/payments/duitku/callback',
        content='amount=1&amount=1000', headers={'content-type': 'application/x-www-form-urlencoded'})
    assert response.status_code == 400
