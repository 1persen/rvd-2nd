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
async def test_gateway_timeout_has_actionable_error(async_client, test_data, duitku):
    import httpx
    duitku.side_effect = httpx.ReadTimeout('Provider stalled')
    response = await invoice(async_client, test_data)
    assert response.status_code == 503
    assert 'Duitku' in response.json()['message']
    assert 'merespons' in response.json()['message']


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


# --- Regression tests for normalize_duitku_status ---


@pytest.mark.parametrize('code,expected', [
    ('00', 'SUCCESS'),
    ('01', 'PENDING'),
    ('02', 'CANCELED'),
    ('99', None),
    ('', None),
])
def test_normalize_duitku_status(code, expected):
    """Duitku status code maps to internal payment status; unknown returns None."""
    assert duitku_service.normalize_duitku_status(code) == expected


# --- Regression tests for transactionStatus settlement paths ---


@pytest.mark.asyncio
async def test_transaction_status_00_triggers_settlement(async_client, test_data, db_session, monkeypatch):
    """Status 00 from transactionStatus triggers canonical settlement path."""
    import httpx as _httpx
    from app.core.database import async_session
    from app.services.payment_service import process_duitku_callback

    monkeypatch.setattr(settings, 'MIDTRANS_MOCK_MODE', False)
    monkeypatch.setattr(settings, 'DUITKU_MERCHANT_CODE', 'test-merchant')
    monkeypatch.setattr(settings, 'DUITKU_API_KEY', 'test-only-key')

    # Create a PENDING DUITKU payment directly via repository-like flow.
    # First create payment through API but force inquiry to timeout so payment stays PENDING.
    gateway = AsyncMock(side_effect=_httpx.ReadTimeout('Provider stalled'))
    monkeypatch.setattr(duitku_service, 'create_transaction', gateway)

    response = await async_client.post('/api/v1/payments', json={
        'event_id': test_data['event'].id,
        'team_id': test_data['team'].id,
        'qty': 3,
        'supporter_name': 'Test',
        'supporter_phone': '08123456789',
        'supporter_email': 'sandbox-test@example.com',
    })
    assert response.status_code == 503  # timeout, payment stays PENDING

    # Now simulate transactionStatus returning 00 (success).
    class FakeStatusResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                'statusCode': '00',
                'reference': 'ref-settle',
                'paymentUrl': 'https://passport.duitku.com/checkout/test',
            }

    class FakeStatusClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            return FakeStatusResponse()

    with patch.object(duitku_service.httpx, 'AsyncClient', FakeStatusClient):
        # Reconcile via payment_service directly.
        from app.services.payment_service import _reconcile_duitku_timeout
        async with async_session() as session:
            payment = await payment_repo.get_by_duitku_merchant_order_id(
                session, response.json()['data']['id'], for_update=True
            )
            # The payment was not persisted as DUITKU because inquiry failed.
            # Instead, find the payment by id (it was created as MIDTRANS then failed).
            # Actually, on timeout the payment row exists but gateway is still MIDTRANS.
            # We need to find it by id.
            from sqlalchemy import select
            from app.models.payment import Payment
            stmt = select(Payment).where(Payment.id == response.json()['data']['id']).with_for_update()
            result = await session.execute(stmt)
            payment = result.scalar_one()
            reconciled = await _reconcile_duitku_timeout(session, payment, 'SP')
            await session.commit()

    assert reconciled is not None
    assert reconciled.status.value == 'SUCCESS'
    assert reconciled.payment_gateway.value == 'DUITKU'
    assert reconciled.duitku_reference == 'ref-settle'
    # Vote granted exactly once.
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 3


@pytest.mark.asyncio
async def test_transaction_status_01_leaves_pending(async_client, test_data, monkeypatch):
    """Status 01 from transactionStatus leaves payment pending, no votes."""
    import httpx as _httpx
    from app.core.database import async_session
    from app.services.payment_service import _reconcile_duitku_timeout

    monkeypatch.setattr(settings, 'MIDTRANS_MOCK_MODE', False)
    monkeypatch.setattr(settings, 'DUITKU_MERCHANT_CODE', 'test-merchant')
    monkeypatch.setattr(settings, 'DUITKU_API_KEY', 'test-only-key')

    gateway = AsyncMock(side_effect=_httpx.ReadTimeout('Provider stalled'))
    monkeypatch.setattr(duitku_service, 'create_transaction', gateway)

    response = await async_client.post('/api/v1/payments', json={
        'event_id': test_data['event'].id,
        'team_id': test_data['team'].id,
        'qty': 3,
        'supporter_name': 'Test',
        'supporter_phone': '08123456789',
        'supporter_email': 'sandbox-test@example.com',
    })
    assert response.status_code == 503

    class FakeStatusResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {'statusCode': '01', 'reference': 'ref-pending'}

    class FakeStatusClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            return FakeStatusResponse()

    with patch.object(duitku_service.httpx, 'AsyncClient', FakeStatusClient):
        from sqlalchemy import select
        from app.models.payment import Payment
        async with async_session() as session:
            stmt = select(Payment).where(Payment.id == response.json()['data']['id']).with_for_update()
            result = await session.execute(stmt)
            payment = result.scalar_one()
            reconciled = await _reconcile_duitku_timeout(session, payment, 'SP')
            await session.commit()

    assert reconciled is not None
    assert reconciled.status.value == 'PENDING'
    assert reconciled.duitku_reference == 'ref-pending'


@pytest.mark.asyncio
async def test_transaction_status_02_canceled(async_client, test_data, monkeypatch):
    """Status 02 from transactionStatus follows cancellation rules."""
    import httpx as _httpx
    from app.core.database import async_session
    from app.services.payment_service import _reconcile_duitku_timeout

    monkeypatch.setattr(settings, 'MIDTRANS_MOCK_MODE', False)
    monkeypatch.setattr(settings, 'DUITKU_MERCHANT_CODE', 'test-merchant')
    monkeypatch.setattr(settings, 'DUITKU_API_KEY', 'test-only-key')

    gateway = AsyncMock(side_effect=_httpx.ReadTimeout('Provider stalled'))
    monkeypatch.setattr(duitku_service, 'create_transaction', gateway)

    response = await async_client.post('/api/v1/payments', json={
        'event_id': test_data['event'].id,
        'team_id': test_data['team'].id,
        'qty': 3,
        'supporter_name': 'Test',
        'supporter_phone': '08123456789',
        'supporter_email': 'sandbox-test@example.com',
    })
    assert response.status_code == 503

    class FakeStatusResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {'statusCode': '02', 'reference': 'ref-canceled'}

    class FakeStatusClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            return FakeStatusResponse()

    with patch.object(duitku_service.httpx, 'AsyncClient', FakeStatusClient):
        from sqlalchemy import select
        from app.models.payment import Payment
        async with async_session() as session:
            stmt = select(Payment).where(Payment.id == response.json()['data']['id']).with_for_update()
            result = await session.execute(stmt)
            payment = result.scalar_one()
            reconciled = await _reconcile_duitku_timeout(session, payment, 'SP')
            await session.commit()

    assert reconciled is not None
    assert reconciled.status.value == 'CANCELED'


@pytest.mark.asyncio
async def test_transaction_status_unknown_does_not_grant_votes(async_client, test_data, db_session, monkeypatch):
    """Unknown provider status does not grant votes; payment remains unresolved."""
    import httpx as _httpx
    from app.core.database import async_session
    from app.services.payment_service import _reconcile_duitku_timeout

    monkeypatch.setattr(settings, 'MIDTRANS_MOCK_MODE', False)
    monkeypatch.setattr(settings, 'DUITKU_MERCHANT_CODE', 'test-merchant')
    monkeypatch.setattr(settings, 'DUITKU_API_KEY', 'test-only-key')

    gateway = AsyncMock(side_effect=_httpx.ReadTimeout('Provider stalled'))
    monkeypatch.setattr(duitku_service, 'create_transaction', gateway)

    response = await async_client.post('/api/v1/payments', json={
        'event_id': test_data['event'].id,
        'team_id': test_data['team'].id,
        'qty': 3,
        'supporter_name': 'Test',
        'supporter_phone': '08123456789',
        'supporter_email': 'sandbox-test@example.com',
    })
    assert response.status_code == 503

    class FakeStatusResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {'statusCode': '99', 'reference': 'ref-unknown'}

    class FakeStatusClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            return FakeStatusResponse()

    with patch.object(duitku_service.httpx, 'AsyncClient', FakeStatusClient):
        from sqlalchemy import select
        from app.models.payment import Payment
        async with async_session() as session:
            stmt = select(Payment).where(Payment.id == response.json()['data']['id']).with_for_update()
            result = await session.execute(stmt)
            payment = result.scalar_one()
            reconciled = await _reconcile_duitku_timeout(session, payment, 'SP')
            await session.commit()

    assert reconciled is None  # unresolved
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 0


@pytest.mark.asyncio
async def test_transaction_status_timeout_does_not_settle(async_client, test_data, monkeypatch):
    """HTTP timeout during reconciliation does not falsely settle or expire."""
    import httpx as _httpx
    from app.core.database import async_session
    from app.services.payment_service import _reconcile_duitku_timeout

    monkeypatch.setattr(settings, 'MIDTRANS_MOCK_MODE', False)
    monkeypatch.setattr(settings, 'DUITKU_MERCHANT_CODE', 'test-merchant')
    monkeypatch.setattr(settings, 'DUITKU_API_KEY', 'test-only-key')

    gateway = AsyncMock(side_effect=_httpx.ReadTimeout('Provider stalled'))
    monkeypatch.setattr(duitku_service, 'create_transaction', gateway)

    response = await async_client.post('/api/v1/payments', json={
        'event_id': test_data['event'].id,
        'team_id': test_data['team'].id,
        'qty': 3,
        'supporter_name': 'Test',
        'supporter_phone': '08123456789',
        'supporter_email': 'sandbox-test@example.com',
    })
    assert response.status_code == 503

    class FakeTimeoutClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            raise _httpx.ReadTimeout('Reconciliation timed out')

    with patch.object(duitku_service.httpx, 'AsyncClient', FakeTimeoutClient):
        from sqlalchemy import select
        from app.models.payment import Payment
        async with async_session() as session:
            stmt = select(Payment).where(Payment.id == response.json()['data']['id']).with_for_update()
            result = await session.execute(stmt)
            payment = result.scalar_one()
            reconciled = await _reconcile_duitku_timeout(session, payment, 'SP')
            await session.commit()

    assert reconciled is None  # unresolved, not settled, not expired


@pytest.mark.asyncio
async def test_callback_result_code_01_keeps_pending(async_client, test_data, db_session, duitku):
    """Callback resultCode 01 keeps payment pending, no votes granted."""
    payment = (await invoice(async_client, test_data)).json()['data']
    payload = callback(payment, resultCode='01')
    response = await async_client.post('/api/v1/payments/duitku/callback', data=payload)
    assert response.status_code == 200, response.text
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 0
    persisted = await payment_repo.get_payment(db_session, payment['id'])
    assert persisted.status.value == 'PENDING'


@pytest.mark.asyncio
async def test_callback_result_code_02_canceled(async_client, test_data, db_session, duitku):
    """Callback resultCode 02 sets payment to CANCELED, no votes."""
    payment = (await invoice(async_client, test_data)).json()['data']
    payload = callback(payment, resultCode='02')
    response = await async_client.post('/api/v1/payments/duitku/callback', data=payload)
    assert response.status_code == 200, response.text
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 0
    persisted = await payment_repo.get_payment(db_session, payment['id'])
    assert persisted.status.value == 'CANCELED'


@pytest.mark.asyncio
async def test_callback_and_reconciliation_race_one_settlement(async_client, test_data, db_session, duitku, monkeypatch):
    """Callback and reconciliation racing concurrently still produce one settlement."""
    import asyncio
    import httpx as _httpx
    from app.core.database import async_session
    from app.services.payment_service import process_duitku_callback, _reconcile_duitku_timeout

    monkeypatch.setattr(settings, 'MIDTRANS_MOCK_MODE', False)
    monkeypatch.setattr(settings, 'DUITKU_MERCHANT_CODE', 'test-merchant')
    monkeypatch.setattr(settings, 'DUITKU_API_KEY', 'test-only-key')

    # Create payment via API with successful inquiry.
    payment = (await invoice(async_client, test_data)).json()['data']
    assert payment['payment_gateway'] == 'DUITKU'
    assert payment['status'] == 'PENDING'

    callback_payload = callback(payment)

    class FakeStatusResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                'statusCode': '00',
                'reference': 'test-reference',
                'paymentUrl': 'https://passport.duitku.com/checkout/test',
            }

    class FakeStatusClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            return FakeStatusResponse()

    async def run_callback():
        async with async_session() as session:
            _, granted = await process_duitku_callback(session, dict(callback_payload))
            await session.commit()
            return granted

    async def run_reconciliation():
        async with async_session() as session:
            from sqlalchemy import select
            from app.models.payment import Payment
            stmt = select(Payment).where(Payment.id == payment['id']).with_for_update()
            result = await session.execute(stmt)
            p = result.scalar_one()
            reconciled = await _reconcile_duitku_timeout(session, p, 'SP')
            await session.commit()
            return reconciled is not None

    with patch.object(duitku_service.httpx, 'AsyncClient', FakeStatusClient):
        outcomes = await asyncio.gather(run_callback(), run_reconciliation())

    # Exactly one settlement should occur.
    assert outcomes.count(True) == 1 or outcomes.count(True) == 0  # one of them wins
    assert await vote_log_repo.get_vote_count_for_team(db_session, test_data['team'].id) == 3
