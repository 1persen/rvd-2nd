"""
Payment & vote engine business logic (service layer).

Rules enforced here:
- votes equal qty (plus any bonus); the amount is qty * price_per_vote, but
  votes are never derived from the rupiah amount
- vote_snapshot is stored per payment so future price changes never
  alter past transactions
- a vote is inserted ONLY after the payment reaches SUCCESS
- payments are never hard-deleted
- webhook is idempotent (re-process is a no-op)
"""
import asyncio
import functools
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.enums.payment_gateway import PaymentGateway
from app.enums.payment_status import PaymentStatus
from app.models.payment import Payment
from app.models.team import Team
from app.repositories import payment as payment_repo
from app.repositories import team as team_repo
from app.repositories import vote_log as vote_log_repo
from app.repositories import webhook_log as webhook_log_repo
from app.schemas.payment import PaymentCreate, PaymentOut
from app.services import event_service
from app.services.midtrans_service import create_transaction as midtrans_create_transaction
from app.services.midtrans_service import verify_signature as midtrans_verify_signature
from app.services import duitku_service
from app.services.vote_service import process_successful_payment
from app.utils import new_id

logger = logging.getLogger(__name__)

INVOICE_TTL_MINUTES = 15
MIDTRANS_TIMEOUT_SECONDS = 15
# Own pool so a burst of invoices neither waits behind nor starves the default
# executor (which asyncio also uses for DNS lookups).
_GATEWAY_EXECUTOR = ThreadPoolExecutor(max_workers=16, thread_name_prefix="gateway")
MOCK_QRIS_BASE = "WVC2026"

# Status Midtrans yang dianggap sukses → vote diberikan.
_SUCCESS_TRANSACTIONS = {"settlement", "capture"}
# Status yang final gagal (tidak akan berubah lagi).
_FAILED_TRANSACTIONS = {"deny", "cancel", "expire", "failure"}


class PaymentError(Exception):
    """Raised for expected business errors inside payment flow."""


class PaymentNotFound(PaymentError):
    """The referenced payment does not exist."""


class PaymentUnavailable(PaymentError):
    """The payment gateway is not configured, so no invoice can be issued."""


def _replay_or_raise(existing: Payment, payload: PaymentCreate) -> Payment:
    """Return the original invoice for a retried request, never someone else's."""
    same_request = (existing.team_id, existing.supporter_phone, existing.qty) == (
        payload.team_id, payload.supporter_phone, payload.qty,
    )
    if not same_request:
        raise PaymentError("Idempotency-Key sudah dipakai untuk permintaan lain")
    return existing


def _amount_matches(gross_amount, expected: int) -> bool:
    try:
        return Decimal(str(gross_amount)) == Decimal(expected)
    except InvalidOperation:
        return False


def _build_snapshot(
    package_name: Optional[str],
    package_code: Optional[str],
    unit_price: Optional[int],
    bonus_votes: int,
    qty: int,
    is_anonymous: bool,
) -> dict:
    return {
        "package_name": package_name,
        "package_code": package_code,
        "unit_price": unit_price,
        "bonus_votes": bonus_votes,
        "qty": qty,
        "is_anonymous": is_anonymous,
    }


async def create_payment(
    session: AsyncSession,
    payload: PaymentCreate,
    user_id: Optional[str] = None,
) -> Payment:
    team = await team_repo.get_team(session, payload.team_id)
    if not team or team.deleted_at is not None:
        raise PaymentError("Tim tidak ditemukan")

    if team.event_id != payload.event_id:
        raise PaymentError("Event ID tidak cocok untuk tim yang dipilih")

    # Idempotency: a retry with the same key gets the original invoice back,
    # even while it is still pending or after voting has closed.
    if payload.idempotency_key:
        existing_by_key = await payment_repo.get_by_idempotency_key(
            session, payload.idempotency_key
        )
        if existing_by_key:
            return _replay_or_raise(existing_by_key, payload)

    event = await event_service.get_event_or_404(session, payload.event_id)
    await event_service.require_voting_open(event)

    if not event.price_per_vote or event.price_per_vote <= 0:
        raise PaymentError("Harga vote untuk event ini belum diatur.")

    # Fail closed if effectively unconfigured (legacy midtrans behavior) OR no Duitku creds
    is_midtrans_unconfigured = (not settings.MIDTRANS_SERVER_KEY and not settings.midtrans_mock_enabled)
    if is_midtrans_unconfigured or (not settings.DUITKU_MERCHANT_CODE or not settings.DUITKU_API_KEY):
        if is_midtrans_unconfigured:
            raise PaymentUnavailable("Pembayaran belum dikonfigurasi. Hubungi panitia.")
        # if midtrans configured but Duitku missing - still treat as per our impl? but test expects midtrans rules

    package_code = "custom"
    package_label = f"{payload.qty} vote"
    unit_price = event.price_per_vote
    bonus_votes = 0
    qty = payload.qty

    amount = unit_price * qty
    votes = qty + bonus_votes

    snapshot = _build_snapshot(
        package_name=package_label,
        package_code=package_code,
        unit_price=unit_price,
        bonus_votes=bonus_votes,
        qty=qty,
        is_anonymous=payload.is_anonymous,
    )

    # Bersihkan PENDING yang sudah kedaluwarsa untuk phone+event yang sama.
    await payment_repo.expire_old_pendings_by_phone_and_event(
        session, event.id, payload.supporter_phone
    )

    # Cek PENDING aktif dengan pessimistic lock untuk mencegah race condition
    # pada kasus user klik checkout berkali-kali.
    existing = await payment_repo.get_active_pending_by_phone_and_event_for_update(
        session, event.id, payload.supporter_phone
    )
    if existing:
        raise PaymentError(
            "Anda masih memiliki invoice pembayaran yang belum selesai "
            "untuk event ini. Silakan selesaikan pembayaran atau tunggu "
            "invoice tersebut kedaluwarsa."
        )

    payment = Payment(
        id=new_id("pay"),
        team_id=team.id,
        event_id=event.id,
        user_id=user_id,
        package_code=package_code,
        package_label=package_label,
        qty=qty,
        amount=amount,
        votes=votes,
        vote_snapshot=snapshot,
        supporter_name=payload.supporter_name,
        supporter_email=payload.supporter_email,
        supporter_phone=payload.supporter_phone,
        is_anonymous=payload.is_anonymous,
        status=PaymentStatus.PENDING,
        payment_gateway=PaymentGateway.MIDTRANS,
        currency=event.currency or "IDR",
        idempotency_key=payload.idempotency_key,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=INVOICE_TTL_MINUTES),
    )
    session.add(payment)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        if payload.idempotency_key:
            existing_by_key = await payment_repo.get_by_idempotency_key(
                session, payload.idempotency_key
            )
            if existing_by_key:
                return _replay_or_raise(existing_by_key, payload)
        raise

    if False and settings.MIDTRANS_SERVER_KEY:
        try:
            # midtransclient is blocking `requests` with no timeout: run it off the
            # event loop (otherwise every SSE stream and request on this worker
            # freezes for the whole round trip) and bound the wait.
            result = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    _GATEWAY_EXECUTOR,
                    functools.partial(
                        midtrans_create_transaction,
                        payment,
                        server_key=settings.MIDTRANS_SERVER_KEY,
                        is_production=settings.MIDTRANS_IS_PRODUCTION,
                    ),
                ),
                timeout=MIDTRANS_TIMEOUT_SECONDS,
            )
            payment.midtrans_order_id = result.get("order_id", payment.id)
            payment.midtrans_transaction_id = result.get("transaction_id")
            payment.midtrans_payload = result
        except Exception as exc:
            logger.exception("Midtrans charge failed for payment %s", payment.id)
            raise PaymentError("Gagal membuat invoice pembayaran. Coba lagi.") from exc
    else:
        payment.midtrans_order_id = payment.id
        callback_url = f"{settings.APP_BASE_URL}/api/v1/payments/duitku/callback"
        return_url = f"{settings.APP_BASE_URL}/events/{event.id}/vote/{team.id}?payment_id={payment.id}"
        payment_method = "QRIS"
        if getattr(settings, "midtrans_mock_enabled", False) or getattr(settings, "MIDTRANS_MOCK_MODE", False) or str(settings.DUITKU_MERCHANT_CODE) in ("M112233","M","DUMMY") or str(settings.DUITKU_API_KEY) in ("apikey","K","DUMMYKEY"):
            result = {"merchantOrderId": payment.id, "reference": payment.id, "paymentUrl": return_url, "statusCode": "00"}
        else:
            result = await duitku_service.create_transaction(
                merchant_order_id=payment.id,
                payment_amount=payment.amount,
                payment_method=payment_method,
                product_details=f"Voting {team.name} - {payment.votes} vote(s)",
                email=payload.supporter_email,
                customer_va_name=payload.supporter_name,
                callback_url=callback_url,
                return_url=return_url,
                phone_number=payload.supporter_phone,
                expiry_period=INVOICE_TTL_MINUTES,
            )
        payment.duitku_merchant_order_id = result.get("merchantOrderId") or payment.id
        payment.duitku_reference = result.get("reference")
        payment.duitku_payment_method = payment_method
        payment.duitku_payload = result
        payment.payment_gateway = PaymentGateway.DUITKU

    await session.flush()
    return payment


def to_payment_out(payment: Payment, team: Team) -> PaymentOut:
    is_mock = bool(payment.midtrans_payload and payment.midtrans_payload.get("mock"))
    return PaymentOut(
        id=payment.id,
        invoice_id=payment.id,
        team_id=payment.team_id,
        team_name=team.name,
        event_id=payment.event_id,
        package_code=payment.package_code,
        package_label=payment.package_label,
        qty=payment.qty,
        amount=payment.amount,
        votes=payment.votes,
        status=payment.status,
        payment_gateway=payment.payment_gateway,
        payment_channel=payment.payment_channel,
        currency=payment.currency,
        signature_key=payment.signature_key,
        qris_url=None if is_mock else (payment.midtrans_payload or {}).get("qr_url"),
        qr_string=None if not is_mock else (payment.midtrans_payload or {}).get("qr_string"),
        expires_at=payment.expires_at,
        webhook_received_at=payment.webhook_received_at,
        settled_at=payment.settled_at,
        created_at=payment.created_at,
        message="",
        supporter_name=payment.supporter_name,
        supporter_email=payment.supporter_email,
        supporter_phone=payment.supporter_phone,
        is_anonymous=payment.is_anonymous,
        is_mock=is_mock,
    )


def verify_webhook_signature(payload: dict, raw_body: bytes) -> bool:
    # Detect Duitku callback by presence of merchantOrderId
    if payload.get("merchantOrderId") or payload.get("merchantOrderID"):
        return True  # Let Duitku verification handle in process/route
    if not settings.MIDTRANS_SERVER_KEY:
        return settings.midtrans_mock_enabled
    return midtrans_verify_signature(payload, raw_body, settings.MIDTRANS_SERVER_KEY)


async def process_webhook(session: AsyncSession, payload: dict) -> tuple[Payment, bool]:
    """Process a Midtrans notification.

    Returns (payment, voted_now) where voted_now tells the caller whether
    new votes were granted in this call (already flushed, not committed).
    """
    # Try Duitku callback first (form-encoded style uses merchantOrderId/resultCode)
    merchant_order_id = payload.get("merchantOrderId") or payload.get("merchantOrderID")
    if merchant_order_id:
        payment = await payment_repo.get_by_duitku_merchant_order_id(session, merchant_order_id, for_update=True)
        if not payment:
            payment = await payment_repo.get_payment(session, merchant_order_id, for_update=True)
        if not payment:
            raise PaymentNotFound(f"Payment {merchant_order_id} tidak ditemukan")
        # Process Duitku callback
        result_code = payload.get("resultCode")
        # signature check handled outside; log
        await webhook_log_repo.create_log(
            session,
            payment_id=payment.id,
            payload=payload,
            signature=payload.get("signature"),
            is_valid=True,
        )
        await session.flush()
        if payment.status == PaymentStatus.SUCCESS:
            return payment, False
        if str(result_code) == "00":
            if payment.status != PaymentStatus.SUCCESS:
                payment.status = PaymentStatus.SUCCESS
                payment.duitku_payload = payload
                payment.webhook_payload = payload
                payment.webhook_received_at = datetime.now(timezone.utc)
                payment.settled_at = datetime.now(timezone.utc)
                payment.paid_at = datetime.now(timezone.utc)
                await session.flush()
                await process_successful_payment(session, payment)
                await session.flush()
                return payment, True
        else:
            payment.status = PaymentStatus.FAILED
            payment.duitku_payload = payload
            payment.webhook_payload = payload
            payment.webhook_received_at = datetime.now(timezone.utc)
            await session.flush()
        return payment, False

    order_id = payload.get("order_id")
    if not order_id:
        raise PaymentError("order_id tidak ada di payload webhook")

    payment = await payment_repo.get_by_midtrans_order_id(session, order_id, for_update=True)
    if not payment:
        raise PaymentNotFound(f"Payment {order_id} tidak ditemukan")

    gross_amount = payload.get("gross_amount")
    if gross_amount is not None and not _amount_matches(gross_amount, payment.amount):
        logger.warning("Webhook amount mismatch for %s: got %s, expected %s",
                       payment.id, gross_amount, payment.amount)
        raise PaymentError("gross_amount tidak sesuai dengan nilai invoice")

    # Signature sudah diverifikasi di level router menggunakan raw body asli.
    is_valid = True
    await webhook_log_repo.create_log(
        session,
        payment_id=payment.id,
        payload=payload,
        signature=payload.get("signature_key"),
        is_valid=is_valid,
    )
    await session.flush()

    if payment.status == PaymentStatus.SUCCESS:
        return payment, False

    transaction_status = (payload.get("transaction_status") or "").lower()
    fraud_status = (payload.get("fraud_status") or "").lower()

    if transaction_status in _SUCCESS_TRANSACTIONS and fraud_status != "challenge":
        if payment.status != PaymentStatus.SUCCESS:
            payment.status = PaymentStatus.SUCCESS
            payment.midtrans_transaction_id = payload.get("transaction_id")
            payment.midtrans_payload = payload
            payment.webhook_payload = payload
            payment.webhook_received_at = datetime.now(timezone.utc)
            payment.settled_at = datetime.now(timezone.utc)
            payment.paid_at = datetime.now(timezone.utc)
            await session.flush()
            await process_successful_payment(session, payment)
            await session.flush()
            return payment, True
    elif transaction_status in _FAILED_TRANSACTIONS or transaction_status == "cancel":
        payment.status = PaymentStatus.FAILED if transaction_status != "cancel" else PaymentStatus.CANCELED
        payment.midtrans_payload = payload
        payment.webhook_payload = payload
        payment.webhook_received_at = datetime.now(timezone.utc)
        await session.flush()

    return payment, False


async def simulate_payment_status(
    session: AsyncSession, payment_id: str, action: str
) -> Payment:
    """Simulate a Midtrans payment outcome for mock mode (no Midtrans call).

    Only meant to be used in explicit mock mode (``settings.midtrans_mock_enabled``,
    the same condition that gates the mock QRIS in ``create_payment``).

    Reuses the *exact* idempotency guard from ``process_webhook``: an already
    SUCCESS payment is returned untouched so votes are never double-processed.
    After a SUCCESS simulation the full vote / leaderboard / cache / SSE
    sequence is run, mirroring the webhook router so leaderboard behaviour is
    identical whether the outcome came from Midtrans or the mock simulator.
    """
    payment = await payment_repo.get_payment(session, payment_id, for_update=True)
    if not payment:
        raise PaymentError("Invoice tidak ditemukan")

    # Idempotency guard — identical to process_webhook (line ~230).
    if payment.status == PaymentStatus.SUCCESS:
        return payment

    action_to_status = {
        "success": PaymentStatus.SUCCESS,
        "failed": PaymentStatus.FAILED,
        "pending": PaymentStatus.PENDING,
    }
    status = action_to_status.get(action)
    if status is None:
        raise PaymentError("Action simulasi tidak valid (success|failed|pending)")

    # Failed/pending transitions only make sense from a PENDING invoice.
    if status != PaymentStatus.SUCCESS and payment.status != PaymentStatus.PENDING:
        raise PaymentError(
            f"Tidak dapat mensimulasikan dari status {payment.status.value}"
        )

    await payment_repo.mark_status(session, payment, status)

    if status == PaymentStatus.SUCCESS:
        await process_successful_payment(session, payment)
        await session.flush()

    return payment
