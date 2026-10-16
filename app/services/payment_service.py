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
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx
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
from app.services.midtrans_service import verify_signature as midtrans_verify_signature
from app.services import duitku_service
from app.services.vote_service import process_successful_payment
from app.utils import new_id

logger = logging.getLogger(__name__)

INVOICE_TTL_MINUTES = 15
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
    """The payment gateway is unconfigured or temporarily unavailable."""


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


async def _reconcile_duitku_timeout(
    session: AsyncSession,
    payment: Payment,
    payment_method: str,
) -> Optional[Payment]:
    """Attempt bounded reconciliation after an uncertain inquiry outcome.

    Reuses the existing merchant order ID (payment.id) and queries Duitku
    transaction status with exponential backoff. Only applies a verified,
    permitted state transition. Returns the payment if reconciliation succeeded,
    or None if the outcome remains unresolved.
    """
    import asyncio

    for attempt in range(1, duitku_service.MAX_RECONCILIATION_ATTEMPTS + 1):
        try:
            status_result = await duitku_service.check_transaction_status(
                merchant_order_id=payment.id,
            )
        except Exception as exc:
            logger.warning(
                "Duitku reconciliation attempt %d/%d failed for payment %s: %s",
                attempt, duitku_service.MAX_RECONCILIATION_ATTEMPTS, payment.id,
                type(exc).__name__,
            )
            if attempt < duitku_service.MAX_RECONCILIATION_ATTEMPTS:
                await asyncio.sleep(
                    duitku_service.RECONCILIATION_BASE_DELAY * (2 ** (attempt - 1))
                )
            continue

        if not isinstance(status_result, dict):
            logger.warning(
                "Duitku reconciliation returned non-object for payment %s (attempt %d)",
                payment.id, attempt,
            )
            if attempt < duitku_service.MAX_RECONCILIATION_ATTEMPTS:
                await asyncio.sleep(
                    duitku_service.RECONCILIATION_BASE_DELAY * (2 ** (attempt - 1))
                )
            continue

        status_code = status_result.get("statusCode")
        reference = status_result.get("reference")
        payment_url = status_result.get("paymentUrl")

        # Validate provider response against internal payment record.
        if status_code not in duitku_service.DUITKU_STATUS_TO_INTERNAL:
            logger.warning(
                "Duitku reconciliation unknown statusCode=%s for payment %s (attempt %d)",
                status_code, payment.id, attempt,
            )
            if attempt < duitku_service.MAX_RECONCILIATION_ATTEMPTS:
                await asyncio.sleep(
                    duitku_service.RECONCILIATION_BASE_DELAY * (2 ** (attempt - 1))
                )
            continue

        if reference and payment.duitku_reference and reference != payment.duitku_reference:
            logger.warning(
                "Duitku reconciliation reference mismatch for payment %s: "
                "provider=%s internal=%s",
                payment.id, reference, payment.duitku_reference,
            )
            raise PaymentError("Referensi pembayaran tidak sesuai dengan invoice")

        internal_status = duitku_service.normalize_duitku_status(status_code)

        if internal_status == "SUCCESS":
            if not duitku_service.valid_payment_url(payment_url):
                logger.warning(
                    "Duitku reconciliation SUCCESS but invalid paymentUrl for payment %s",
                    payment.id,
                )
                raise PaymentError("URL pembayaran dari Duitku tidak valid")
            payment.duitku_merchant_order_id = payment.id
            payment.duitku_reference = reference or payment.duitku_reference
            payment.duitku_payment_method = payment_method
            payment.duitku_payload = status_result
            payment.payment_gateway = PaymentGateway.DUITKU
            payment.status = PaymentStatus.SUCCESS
            now = datetime.now(timezone.utc)
            payment.settled_at = now
            payment.paid_at = now
            payment.webhook_received_at = now
            await session.flush()
            await process_successful_payment(session, payment)
            await session.flush()
            return payment

        if internal_status == "PENDING":
            # Keep payment pending; record provider reference if available.
            payment.duitku_merchant_order_id = payment.id
            payment.duitku_reference = reference or payment.duitku_reference
            payment.duitku_payment_method = payment_method
            payment.duitku_payload = status_result
            payment.payment_gateway = PaymentGateway.DUITKU
            await session.flush()
            return payment

        if internal_status == "CANCELED":
            payment.status = PaymentStatus.CANCELED
            payment.duitku_payload = status_result
            await session.flush()
            return payment

    # All attempts exhausted; payment remains in its current state (PENDING).
    logger.warning(
        "Duitku reconciliation exhausted (%d attempts) for payment %s; "
        "payment remains unresolved",
        duitku_service.MAX_RECONCILIATION_ATTEMPTS, payment.id,
    )
    return None


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

    if not settings.payments_configured:
        raise PaymentUnavailable("Pembayaran belum dikonfigurasi. Hubungi panitia.")

    if not settings.midtrans_mock_enabled:
        email = (payload.supporter_email or "").strip()
        if len(email) > 50 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise PaymentError("Isi alamat email yang valid (maksimal 50 karakter) untuk pembayaran.")
        payload.supporter_email = email

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

    if settings.midtrans_mock_enabled:
        payment.midtrans_order_id = payment.id
        payment.midtrans_payload = {
            "mock": True,
            "qr_string": f"{MOCK_QRIS_BASE}:{event.id}:{payment.id}:{payment.amount}:{payment.votes}",
        }
    else:
        try:
            result = await duitku_service.create_transaction(
                merchant_order_id=payment.id,
                payment_amount=payment.amount,
                payment_method=settings.DUITKU_PAYMENT_METHOD,
                product_details=f"Voting {team.name} - {payment.votes} vote(s)",
                email=payload.supporter_email,
                customer_va_name=payload.supporter_name,
                callback_url=f"{settings.APP_BASE_URL}/api/v1/payments/duitku/callback",
                return_url=f"{settings.APP_BASE_URL}/events/{event.id}/vote/{team.id}",
                phone_number=payload.supporter_phone,
                expiry_period=INVOICE_TTL_MINUTES,
            )
            if (not isinstance(result, dict) or result.get("statusCode") != "00"
                    or not result.get("reference")
                    or not duitku_service.valid_payment_url(result.get("paymentUrl"))):
                raise ValueError("Invalid Duitku inquiry response")
        except httpx.TimeoutException as exc:
            logger.warning("Duitku inquiry timed out for payment %s (method=%s)",
                           payment.id, settings.DUITKU_PAYMENT_METHOD)
            # Timeout = uncertain outcome. Do NOT mark EXPIRED; the provider may
            # have created the transaction even though RVD did not receive the
            # response. Attempt bounded reconciliation before giving up.
            reconciliation_result = await _reconcile_duitku_timeout(
                session, payment, settings.DUITKU_PAYMENT_METHOD
            )
            if reconciliation_result is not None:
                return reconciliation_result
            # Payment remains PENDING for later retry; do not create a duplicate
            # order and do not mark it expired based solely on a timeout.
            raise PaymentUnavailable(
                "Duitku belum merespons. Tunggu beberapa saat sebelum mencoba lagi."
            ) from exc
        except httpx.HTTPStatusError as exc:
            # HTTP 502/503/504 = uncertain outcome; do not mark EXPIRED.
            logger.warning("Duitku inquiry HTTP error for payment %s (method=%s, status=%s)",
                           payment.id, settings.DUITKU_PAYMENT_METHOD, exc.response.status_code)
            reconciliation_result = await _reconcile_duitku_timeout(
                session, payment, settings.DUITKU_PAYMENT_METHOD
            )
            if reconciliation_result is not None:
                return reconciliation_result
            raise PaymentUnavailable(
                "Duitku belum merespons. Tunggu beberapa saat sebelum mencoba lagi."
            ) from exc
        except Exception as exc:
            logger.warning("Duitku inquiry failed for payment %s (%s)", payment.id, type(exc).__name__)
            raise PaymentError("Gagal membuat invoice pembayaran. Coba lagi.") from exc
        payment.duitku_merchant_order_id = payment.id
        payment.duitku_reference = result["reference"]
        payment.duitku_payment_method = settings.DUITKU_PAYMENT_METHOD
        payment.duitku_payload = result
        payment.payment_gateway = PaymentGateway.DUITKU

    await session.flush()
    return payment


def to_payment_out(payment: Payment, team: Team) -> PaymentOut:
    is_mock = bool(payment.midtrans_payload and payment.midtrans_payload.get("mock"))
    duitku_payload = payment.duitku_payload or {}
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
        qr_string=(payment.midtrans_payload or {}).get("qr_string") if is_mock else duitku_payload.get("qrString"),
        payment_url=duitku_payload.get("paymentUrl") if duitku_service.valid_payment_url(duitku_payload.get("paymentUrl")) else None,
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
        is_sandbox=(payment.payment_gateway == PaymentGateway.DUITKU
                    and duitku_service.is_sandbox_payment_url(duitku_payload.get("paymentUrl"))),
    )


def verify_webhook_signature(payload: dict, raw_body: bytes) -> bool:
    # Gateway callbacks cannot be routed through another gateway's verifier.
    if "merchantOrderId" in payload or "merchantOrderID" in payload:
        return False
    if not settings.MIDTRANS_SERVER_KEY:
        return settings.midtrans_mock_enabled
    return midtrans_verify_signature(payload, raw_body, settings.MIDTRANS_SERVER_KEY)


async def process_webhook(session: AsyncSession, payload: dict) -> tuple[Payment, bool]:
    """Process a Midtrans notification.

    Returns (payment, voted_now) where voted_now tells the caller whether
    new votes were granted in this call (already flushed, not committed).
    """
    order_id = payload.get("order_id")
    if not order_id:
        raise PaymentError("order_id tidak ada di payload webhook")

    payment = await payment_repo.get_by_midtrans_order_id(session, order_id, for_update=True)
    if not payment:
        raise PaymentNotFound(f"Payment {order_id} tidak ditemukan")

    if payment.payment_gateway != PaymentGateway.MIDTRANS:
        raise PaymentError("Gateway invoice tidak sesuai")
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


async def process_duitku_callback(session: AsyncSession, payload: dict) -> tuple[Payment, bool]:
    # Verify again at the service boundary so internal callers cannot skip it.
    if not duitku_service.valid_callback(payload):
        raise PaymentError("Signature tidak valid")
    payment = await payment_repo.get_by_duitku_merchant_order_id(
        session, payload["merchantOrderId"], for_update=True
    )
    if not payment:
        raise PaymentNotFound("Invoice tidak ditemukan")
    if payment.payment_gateway != PaymentGateway.DUITKU:
        raise PaymentError("Gateway invoice tidak sesuai")
    if not _amount_matches(payload["amount"], payment.amount):
        raise PaymentError("Jumlah pembayaran tidak sesuai dengan invoice")
    if not payment.duitku_reference or payload.get("reference") != payment.duitku_reference:
        raise PaymentError("Referensi pembayaran tidak sesuai")
    result_code = payload.get("resultCode")
    if result_code not in ("00", "01", "02"):
        raise PaymentError("Status pembayaran tidak dikenal")
    if payment.status == PaymentStatus.SUCCESS:
        return payment, False
    await webhook_log_repo.create_log(
        session, payment_id=payment.id, payload=payload,
        signature=payload["signature"], is_valid=True,
    )
    payment.webhook_payload = payload
    payment.webhook_received_at = datetime.now(timezone.utc)
    # Keep the inquiry payload (checkout URL and QR) separate from callbacks.
    if result_code == "00":
        payment.status = PaymentStatus.SUCCESS
        payment.settled_at = payment.webhook_received_at
        payment.paid_at = payment.webhook_received_at
        await session.flush()
        await process_successful_payment(session, payment)
        await session.flush()
        return payment, True
    if result_code == "01":
        # Pending/processing: keep payment unresolved, do not grant votes.
        await session.flush()
        return payment, False
    # result_code == "02": canceled.
    payment.status = PaymentStatus.CANCELED
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
