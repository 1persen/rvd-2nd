"""Duitku checkout and callback integrity against the disposable database."""
import asyncio
import hashlib
import hmac
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.config import settings
from app.core.database import async_session
from app.enums.payment_status import PaymentStatus
from app.repositories import payment as payment_repo, vote_log as vote_log_repo
from app.services import duitku_service, leaderboard_service, payment_service


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
async def test_gateway_timeout_keeps_invoice_unresolved(async_client, test_data, duitku):
    import httpx
    duitku.side_effect = httpx.ReadTimeout('Provider stalled')
    response = await invoice(async_client, test_data)
    # Timeout = hasil tidak pasti: invoice tetap PENDING, tanpa tautan bayar.
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['status'] == 'PENDING'
    assert data['id']
    assert data['payment_url'] is None
    assert data['inquiry_state'] == 'unknown'

    duitku.side_effect = None
    retry = await invoice(async_client, test_data)
    assert retry.status_code == 400, retry.text
    assert duitku.await_count == 1


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
    assert duitku.call_args.kwargs['payment_method'] == 'NQ'


@pytest.mark.asyncio
async def test_chosen_payment_method_is_sent_to_inquiry(async_client, test_data, duitku):
    response = await invoice(async_client, test_data, payment_method='SA')
    assert response.status_code == 200, response.text
    assert duitku.call_args.kwargs['payment_method'] == 'SA'


@pytest.mark.asyncio
async def test_unknown_payment_method_is_rejected_before_inquiry(async_client, test_data, duitku):
    response = await invoice(async_client, test_data, payment_method='XX')
    assert response.status_code == 400, response.text
    duitku.assert_not_awaited()


@pytest.mark.asyncio
async def test_method_list_is_static_without_provider_call(async_client, test_data, duitku):
    response = await async_client.get('/api/v1/payments/methods')
    assert response.status_code == 200, response.text
    methods = {m['code']: m['name'] for m in response.json()['data']}
    assert methods == {'NQ': 'QRIS', 'SA': 'ShopeePay', 'DA': 'DANA'}
    duitku.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_requires_valid_token(async_client, test_data, duitku):
    created = await invoice(async_client, test_data)
    assert created.status_code == 200, created.text
    data = created.json()['data']
    payment_id, token = data['id'], data['status_token']
    assert token
    url = f'/api/v1/payments/{payment_id}/status'

    assert (await async_client.get(url)).status_code == 403
    assert (await async_client.get(url, params={'t': 'x' * 32})).status_code == 403
    allowed = await async_client.get(url, params={'t': token})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()['data']['id'] == payment_id


@pytest.mark.asyncio
async def test_reconcile_requires_valid_token(async_client, test_data, duitku):
    created = await invoice(async_client, test_data)
    assert created.status_code == 200, created.text
    with patch.object(duitku_service, 'check_transaction_status', new=AsyncMock()) as status_check:
        denied = await async_client.post(f"/api/v1/payments/{created.json()['data']['id']}/reconcile")
        assert denied.status_code == 403
        status_check.assert_not_awaited()


@pytest.mark.asyncio
async def test_return_url_contains_status_token(async_client, test_data, duitku):
    from urllib.parse import parse_qs, urlparse

    created = await invoice(async_client, test_data)
    assert created.status_code == 200, created.text
    payment_id = created.json()['data']['id']
    return_url = duitku.call_args.kwargs['return_url']
    assert 't=' in return_url
    assert parse_qs(urlparse(return_url).query)['t'] == [payment_service.status_token(payment_id)]


@pytest.mark.asyncio
async def test_status_response_hides_payment_url_when_not_pending(async_client, test_data, duitku, db_session):
    created = await invoice(async_client, test_data)
    assert created.status_code == 200, created.text
    data = created.json()['data']
    async with async_session() as fresh:
        stored = await payment_repo.get_payment(fresh, data['id'])
        stored.status = PaymentStatus.SUCCESS
        await fresh.commit()
    checked = await async_client.get(f"/api/v1/payments/{data['id']}/status", params={'t': data['status_token']})
    assert checked.status_code == 200, checked.text
    assert checked.json()['data']['payment_url'] is None


@pytest.mark.asyncio
async def test_timeout_does_not_release_idempotency_key(async_client, test_data, duitku, db_session):
    import httpx
    duitku.side_effect = httpx.ReadTimeout('Provider stalled')
    response = await invoice(async_client, test_data, idempotency_key=test_data['event'].id)
    assert response.status_code == 200, response.text
    async with async_session() as fresh:
        payment = (await payment_repo.list_payments(fresh, event_id=test_data['event'].id, limit=1))[0]
        assert payment.idempotency_key == test_data['event'].id


@pytest.mark.asyncio
async def test_rejected_inquiry_fails_and_method_can_switch(async_client, test_data, duitku, db_session):
    duitku.return_value = {'statusCode': '01'}
    response = await invoice(async_client, test_data, idempotency_key=test_data['event'].id)
    # Penolakan definitif: FAILED dan idempotency key dilepas.
    assert response.status_code == 400, response.text
    async with async_session() as fresh:
        payment = (await payment_repo.list_payments(fresh, event_id=test_data['event'].id, limit=1))[0]
        assert payment.status == PaymentStatus.FAILED
        assert payment.idempotency_key is None

    duitku.return_value = {'statusCode': '00', 'reference': 'retry',
                           'paymentUrl': 'https://passport.duitku.com/checkout/test',
                           'qrString': 'test-qr'}
    retry = await invoice(async_client, test_data, payment_method='DA',
                          idempotency_key=test_data['event'].id)
    assert retry.status_code == 200, retry.text
    assert duitku.call_args.kwargs['payment_method'] == 'DA'


@pytest.mark.asyncio
async def test_malformed_success_stays_unknown_and_blocks_retry(async_client, test_data, duitku):
    duitku.return_value = {'statusCode': '00', 'reference': 'r', 'paymentUrl': 'javascript:alert(1)'}
    response = await invoice(async_client, test_data, idempotency_key=test_data['event'].id)
    # Sukses cacat = tidak pasti: invoice PENDING tanpa tautan bayar.
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['status'] == 'PENDING'
    assert data['payment_url'] is None
    assert data['inquiry_state'] == 'unknown'

    duitku.return_value = {'statusCode': '00', 'reference': 'retry',
                           'paymentUrl': 'https://passport.duitku.com/checkout/test'}
    # Replay dengan idempotency key yang sama: invoice lama dikembalikan, tanpa order provider baru.
    replay = await invoice(async_client, test_data, idempotency_key=test_data['event'].id)
    assert replay.status_code == 200, replay.text
    assert replay.json()['data']['id'] == data['id']
    assert replay.json()['data']['payment_url'] is None
    assert duitku.await_count == 1

    # Retry tanpa idempotency key diblokir selama invoice belum pasti.
    blocked = await invoice(async_client, test_data)
    assert blocked.status_code == 400, blocked.text
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
async def test_confirmed_payment_publishes_support_once(async_client, test_data, duitku, monkeypatch):
    """Satu pembayaran sukses menerbitkan tepat satu event support publik."""
    published = []

    async def capture(payload):
        published.append(payload)

    monkeypatch.setattr(leaderboard_service.cache, 'publish', capture)
    response = await invoice(
        async_client, test_data, qty=10, effect_code='bintang',
        support_message='Semangat untuk jagoanmu!',
    )
    assert response.status_code == 200, response.text
    payment = response.json()['data']
    assert payment['effect_code'] == 'bintang'
    # Efek hanya bundling: tidak menambah biaya maupun jumlah vote.
    assert payment['amount'] == 10 * test_data['event'].price_per_vote
    assert payment['votes'] == 10

    for _ in range(2):
        delivered = await async_client.post('/api/v1/payments/duitku/callback', data=callback(payment))
        assert delivered.status_code == 200, delivered.text

    supports = [p for p in published if p.get('type') == 'support']
    assert len(supports) == 1
    assert supports[0]['payment_id'] == payment['id']
    assert supports[0]['effect_code'] == 'bintang'
    assert supports[0]['support_message'] == 'Semangat untuk jagoanmu!'
    assert 'supporter_phone' not in supports[0]
    assert 'supporter_email' not in supports[0]


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
@pytest.mark.parametrize('environment', ['sandbox', 'production'])
async def test_checkout_page_has_no_sandbox_banner(async_client, test_data, duitku, monkeypatch, environment):
    monkeypatch.setattr(settings, 'DUITKU_ENVIRONMENT', environment)
    response = await async_client.get(f"/events/{test_data['event'].id}/vote/{test_data['team'].id}")
    # Banner sandbox di halaman publik dihapus; penanda environment kini hanya
    # di respons invoice (is_sandbox), bukan di markup halaman.
    assert 'Mode sandbox Duitku' not in response.text
    assert 'data-payment-environment="sandbox"' not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [[], None, {'signature': 123}, {
    'merchantCode': 'test-merchant', 'merchantOrderId': 'id', 'amount': '1000', 'signature': 'ÃƒÂ©' * 64,
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


# --- Plan 03: callback durability (webhook inbox) ---


def _save_inbox(monkeypatch, store):
    from app.repositories import webhook_log as webhook_log_repo
    original = webhook_log_repo.create_log

    async def wrapped(session, **kwargs):
        log = await original(session, **kwargs)
        store['inbox_id'] = log.id
        return log

    monkeypatch.setattr(webhook_log_repo, 'create_log', wrapped)


@pytest.mark.asyncio
async def test_callback_for_unknown_order_is_stored_not_404(async_client, test_data, db_session, duitku, monkeypatch):
    import uuid

    from app.models.webhook_log import WebhookLog

    store = {}
    _save_inbox(monkeypatch, store)
    # ID unik per run: database test tidak dibersihkan antar eksekusi.
    order_id = f'pay-unknown-{uuid.uuid4().hex[:12]}'
    payload = callback({'id': order_id, 'amount': 15000})
    response = await async_client.post('/api/v1/payments/duitku/callback', data=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['success'] is True
    assert body['message'] == 'Callback diterima'

    assert 'inbox_id' in store
    async with async_session() as fresh:
        log = await fresh.get(WebhookLog, store['inbox_id'])
    assert log is not None
    assert log.payment_id is None
    assert log.merchant_order_id == order_id
    assert log.provider == 'duitku'
    assert log.status == 'unmatched'
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 0
    assert await payment_repo.list_payments(db_session, event_id=test_data['event'].id, limit=10) == []


@pytest.mark.asyncio
async def test_callback_is_logged_with_provider_duitku(async_client, test_data, db_session, duitku):
    from sqlalchemy import select
    from app.models.webhook_log import WebhookLog

    payment = (await invoice(async_client, test_data)).json()['data']
    response = await async_client.post('/api/v1/payments/duitku/callback', data=callback(payment))
    assert response.status_code == 200, response.text

    async with async_session() as fresh:
        log = (await fresh.execute(
            select(WebhookLog).where(WebhookLog.payment_id == payment['id'])
        )).scalars().one()
    assert log.provider == 'duitku'
    assert log.merchant_order_id == payment['id']
    assert log.status == 'processed'


@pytest.mark.asyncio
async def test_callback_uppercase_signature_is_accepted(async_client, test_data, duitku):
    payment = (await invoice(async_client, test_data)).json()['data']
    payload = callback(payment)
    assert payload['signature'] == payload['signature'].lower()
    payload['signature'] = payload['signature'].upper()
    response = await async_client.post('/api/v1/payments/duitku/callback', data=payload)
    assert response.status_code == 200, response.text
    async with async_session() as fresh:
        assert await vote_log_repo.get_vote_count_for_team(fresh, test_data['team'].id) == 3


@pytest.mark.asyncio
async def test_callback_success_after_timeout_settles_once(async_client, test_data, db_session, duitku):
    # Callback without reference asks the service to reconcile; simulate provider
    # without touching the degraded ``check_transaction_status`` fixture.
    from app.services import payment_service
    reconcile = AsyncMock()

    async def fake_reconcile(session, payment_id):
        found = await payment_repo.get_payment(session, payment_id, for_update=True)
        found.duitku_reference = 'reconciled-reference'
        await session.flush()
        return payment_service.ReconcileResult(
            found, False, payment_service.ReconcileVerdict.PRESENT
        )

    reconcile.side_effect = fake_reconcile
    duitku.side_effect = httpx.ReadTimeout('Provider stalled')
    await invoice(async_client, test_data, idempotency_key=test_data['event'].id)
    payment = (await payment_repo.list_payments(db_session, event_id=test_data['event'].id, limit=1))[0]
    assert payment.duitku_payload['_inquiry_state'] == 'unknown'

    payload = callback({'id': payment.id, 'amount': payment.amount}, reference='reconciled-reference')
    with patch.object(payment_service, 'reconcile_duitku_payment', reconcile):
        payment.duitku_reference = None
        await db_session.flush()
        for _ in range(2):
            response = await async_client.post('/api/v1/payments/duitku/callback', data=payload)
            assert response.status_code == 200, response.text
    async with async_session() as fresh:
        assert await vote_log_repo.get_vote_count_for_team(fresh, test_data['team'].id) == 3
        settled = await payment_repo.get_payment(fresh, payment.id)
    assert settled.status == PaymentStatus.SUCCESS


@pytest.mark.asyncio
async def test_callback_success_on_expired_payment_settles(async_client, test_data, db_session, duitku):
    payment = (await invoice(async_client, test_data)).json()['data']
    stored = await payment_repo.get_payment(db_session, payment['id'], for_update=True)
    stored.status = PaymentStatus.EXPIRED
    await db_session.commit()

    response = await async_client.post('/api/v1/payments/duitku/callback', data=callback(payment))
    assert response.status_code == 200, response.text
    async with async_session() as fresh:
        assert (await payment_repo.get_payment(fresh, payment['id'])).status == PaymentStatus.SUCCESS
        assert await vote_log_repo.get_vote_count_for_team(fresh, test_data['team'].id) == 3


@pytest.mark.asyncio
async def test_callback_pending_after_success_does_not_downgrade(async_client, test_data, duitku):
    payment = (await invoice(async_client, test_data)).json()['data']
    response = await async_client.post('/api/v1/payments/duitku/callback', data=callback(payment))
    assert response.status_code == 200, response.text
    response = await async_client.post(
        '/api/v1/payments/duitku/callback', data=callback(payment, resultCode='01')
    )
    assert response.status_code == 200, response.text
    async with async_session() as fresh:
        assert (await payment_repo.get_payment(fresh, payment['id'])).status == PaymentStatus.SUCCESS
        assert await vote_log_repo.get_vote_count_for_team(fresh, test_data['team'].id) == 3
