"""Duitku checkout and callback integrity against the disposable database."""
import asyncio
import hashlib
import hmac
from unittest.mock import AsyncMock, patch

import httpx
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
    monkeypatch.setattr(duitku_service, 'check_transaction_status', AsyncMock(side_effect=httpx.ReadTimeout('Status unavailable')))
    # Daftar metode aktif dari Duitku (read-only). Mock agar tes tidak memanggil sandbox.
    monkeypatch.setattr(duitku_service, 'get_payment_methods', AsyncMock(return_value=[
        {'paymentMethod': 'NQ', 'paymentName': 'NOBU QRIS', 'paymentImage': '', 'totalFee': '0'},
        {'paymentMethod': 'SA', 'paymentName': 'SHOPEEPAY APP', 'paymentImage': '', 'totalFee': '200'},
    ]))
    return gateway


async def invoice(client, data, **overrides):
    return await client.post('/api/v1/payments', json={
        'event_id': data['event'].id, 'team_id': data['team'].id, 'qty': 3,
        'supporter_name': 'Test', 'supporter_phone': '08123456789',
        'supporter_email': 'sandbox-test@example.com', **overrides,
    })


@pytest.mark.asyncio
@pytest.mark.parametrize('email', [None, '', '   ', 'invalid', 'test@', 'a b@example.com', 'a' * 40 + '@example.com'])
async def test_duitku_requires_valid_email_before_contacting_gateway(async_client, test_data, duitku, email):
    response = await invoice(async_client, test_data, supporter_email=email)
    assert response.status_code == 400
    assert 'email' in response.json()['message'].lower()
    duitku.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_timeout_returns_saved_order_with_actionable_message(async_client, test_data, duitku):
    import httpx
    duitku.side_effect = httpx.ReadTimeout('Provider stalled')
    response = await invoice(async_client, test_data)
    assert response.status_code == 200
    assert response.json()['data']['status'] == 'PENDING'
    assert 'Duitku' in response.json()['data']['message']
    assert 'disimpan' in response.json()['data']['message']


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
    assert duitku.call_args.kwargs['payment_method'] == settings.DUITKU_PAYMENT_METHOD


@pytest.mark.asyncio
async def test_chosen_payment_method_is_sent_to_inquiry(async_client, test_data, duitku):
    response = await invoice(async_client, test_data, payment_method='SA')
    assert response.status_code == 200, response.text
    assert duitku.call_args.kwargs['payment_method'] == 'SA'


@pytest.mark.asyncio
async def test_inactive_payment_method_is_rejected_before_inquiry(async_client, test_data, duitku):
    response = await invoice(async_client, test_data, payment_method='SP')
    assert response.status_code == 400, response.text
    duitku.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [{'statusCode': '01'}, {'statusCode': '00', 'reference': 'r', 'paymentUrl': 'javascript:alert(1)'}])
async def test_unusable_inquiry_retains_order_for_safe_retry(async_client, test_data, duitku, bad):
    duitku.return_value = bad
    response = await invoice(async_client, test_data, idempotency_key=test_data['event'].id)
    assert response.status_code == 200
    assert response.json()['data']['payment_url'] is None
    # A malformed response must not cause another provider order on retry.
    duitku.return_value = {'statusCode': '00', 'reference': 'retry', 'paymentUrl': 'https://passport.duitku.com/checkout/test'}
    retry = await invoice(async_client, test_data, idempotency_key=test_data['event'].id)
    assert retry.status_code == 200, retry.text
    assert retry.json()['data']['id'] == response.json()['data']['id']
    assert duitku.await_count == 1


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
    assert 'Lanjutkan pembayaran' in response.text
    assert 'Buka pembayaran Duitku' not in response.text
    assert 'Periksa status' not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('host,is_sandbox', [('sandbox.duitku.com', True), ('passport.duitku.com', False)])
async def test_invoice_environment_follows_saved_checkout_url(async_client, test_data, duitku, monkeypatch, host, is_sandbox):
    # Old invoices retain their identity even after the server environment changes.
    monkeypatch.setattr(settings, 'DUITKU_ENVIRONMENT', 'production' if is_sandbox else 'sandbox')
    duitku.return_value['paymentUrl'] = f'https://{host}/checkout/test'
    payment = (await invoice(async_client, test_data)).json()['data']
    assert payment['is_sandbox'] is is_sandbox
    assert payment['is_mock'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('environment,visible', [('sandbox', True), ('production', False)])
async def test_checkout_discloses_sandbox_before_invoice(async_client, test_data, duitku, monkeypatch, environment, visible):
    monkeypatch.setattr(settings, 'DUITKU_ENVIRONMENT', environment)
    response = await async_client.get(f"/events/{test_data['event'].id}/vote/{test_data['team'].id}")
    assert ('data-payment-environment="sandbox"' in response.text) is visible


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


# --- Regression tests for callback signature ---


@pytest.mark.asyncio
async def test_verify_callback_signature_no_name_error():
    """verify_callback_signature harus mengembalikan boolean, tidak raise NameError."""
    result = duitku_service.verify_callback_signature(
        merchant_code='test-merchant',
        amount='15000',
        merchant_order_id='pay-123',
        received_signature='dummy',
        api_key='test-key',
    )
    assert isinstance(result, bool)


@pytest.mark.asyncio
async def test_verify_callback_signature_valid_and_invalid():
    """Signature valid menghasilkan True, signature salah menghasilkan False."""
    merchant_code = 'test-merchant'
    amount = '15000'
    merchant_order_id = 'pay-123'
    api_key = 'test-key'

    message = merchant_code + amount + merchant_order_id
    valid_sig = hmac.new(api_key.encode(), message.encode(), hashlib.sha256).hexdigest()

    assert duitku_service.verify_callback_signature(
        merchant_code, amount, merchant_order_id, valid_sig, api_key
    ) is True

    assert duitku_service.verify_callback_signature(
        merchant_code, amount, merchant_order_id, '0' * 64, api_key
    ) is False

    # Perubahan nominal membuat signature tidak cocok
    assert duitku_service.verify_callback_signature(
        merchant_code, '99999', merchant_order_id, valid_sig, api_key
    ) is False

    # Perubahan order ID membuat signature tidak cocok
    assert duitku_service.verify_callback_signature(
        merchant_code, amount, 'pay-999', valid_sig, api_key
    ) is False


# --- Regression tests for transactionStatus signature ---


def test_generate_transaction_status_signature_excludes_amount():
    """Signing string transactionStatus hanya berisi merchant code + order ID, tanpa nominal."""
    sig = duitku_service.generate_transaction_status_signature(
        merchant_code='test-merchant',
        merchant_order_id='pay-123',
        api_key='test-key',
    )
    expected = hmac.new(
        b'test-key',
        b'test-merchantpay-123',
        hashlib.sha256,
    ).hexdigest()
    assert sig == expected
    assert sig == sig.lower()  # hexadecimal lowercase


# --- Regression tests for check_transaction_status request ---


@pytest.mark.asyncio
async def test_check_transaction_status_url_follows_environment(monkeypatch):
    """URL transactionStatus mengikuti DUITKU_ENVIRONMENT."""
    monkeypatch.setattr(settings, 'DUITKU_MERCHANT_CODE', 'test-merchant')
    monkeypatch.setattr(settings, 'DUITKU_API_KEY', 'test-key')

    captured = {}

    class FakeResponse:
        def __init__(self, url, json_data):
            self.url = url
            self._json = json_data
            self.status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return self._json

    class FakeClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            captured['url'] = url
            captured['payload'] = json
            return FakeResponse(url, {'statusCode': '00', 'reference': 'ref-1'})

    with patch.object(duitku_service.httpx, 'AsyncClient', FakeClient):
        # Sandbox
        monkeypatch.setattr(settings, 'DUITKU_ENVIRONMENT', 'sandbox')
        await duitku_service.check_transaction_status(merchant_order_id='pay-123')
        assert captured['url'] == 'https://sandbox.duitku.com/webapi/api/merchant/transactionStatus'
        assert captured['payload']['merchantCode'] == 'test-merchant'
        assert captured['payload']['merchantOrderId'] == 'pay-123'
        assert 'signature' in captured['payload']

        # Production
        monkeypatch.setattr(settings, 'DUITKU_ENVIRONMENT', 'production')
        await duitku_service.check_transaction_status(merchant_order_id='pay-123')
        assert captured['url'] == 'https://passport.duitku.com/webapi/api/merchant/transactionStatus'


@pytest.mark.asyncio
async def test_check_transaction_status_handles_errors(monkeypatch):
    """HTTP error dan JSON invalid tidak dianggap sebagai pembayaran sukses."""
    monkeypatch.setattr(settings, 'DUITKU_MERCHANT_CODE', 'test-merchant')
    monkeypatch.setattr(settings, 'DUITKU_API_KEY', 'test-key')

    class FakeResponse:
        status_code = 500

        def raise_for_status(self):
            raise httpx.HTTPStatusError('Server error', request=None, response=self)

        def json(self):
            return {}

    class FakeClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            return FakeResponse()

    with patch.object(duitku_service.httpx, 'AsyncClient', FakeClient):
        with pytest.raises(httpx.HTTPStatusError):
            await duitku_service.check_transaction_status(merchant_order_id='pay-123')
