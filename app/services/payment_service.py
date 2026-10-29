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
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import PAYMENT_METHODS, PAYMENT_METHOD_CODES, settings
from app.core.effects import EFFECTS
from app.enums.payment_gateway import PaymentGateway
from app.services import profanity_filter
from app.enums.payment_status import PaymentStatus
from app.models.payment import Payment
from app.models.event import Event
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
STATUS_CHECK_INTERVAL_SECONDS = 60
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
    # Effect and message are part of the order: a retry that changes them is a
    # different request and must not silently receive the old invoice.
    same_request = (
        existing.team_id, existing.supporter_phone, existing.qty,
        existing.effect_code, existing.support_message,
    ) == (
        payload.team_id, payload.supporter_phone, payload.qty,
        payload.effect_code or None, profanity_filter.censor((payload.support_message or "").strip()) or None,
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


def public_payment_methods() -> list[dict]:
    """Pilihan metode untuk modal: daftar tetap, tanpa inquiry ke Duitku.

    Kode dikirim ke Duitku sebagai `paymentMethod` saat invoice dibuat. Biaya
    admin (jika ada) ditentukan Duitku di halaman pembayarannya, bukan di sini.
    """
    return [{"code": m["code"], "name": m["label"]} for m in PAYMENT_METHODS]


def resolve_payment_method(code: str | None) -> str:
    """Kode metode dari frontend; kosong berarti QRIS."""
    if not code:
        return PAYMENT_METHODS[0]["code"]
    if code not in PAYMENT_METHOD_CODES:
        raise PaymentError("Metode pembayaran tidak dikenal. Pilih QRIS, ShopeePay, atau DANA.")
    return code


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

    # New invoices are only for publicly visible events; replaying an existing
    # invoice above is intentionally allowed even after the event is archived.
    event = await event_service.get_public_event_or_404(session, payload.event_id)
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

    # Gift adalah paket bundling: memilih gift berarti membeli paket vote-nya,
    # dan efek animasi adalah bonus paket (bukan biaya dan bukan vote tambahan).
    effect_code = payload.effect_code or None
    if effect_code is not None:
        gift = EFFECTS.get(effect_code)
        if gift is None:
            raise PaymentError("Gift animasi tidak dikenal.")
        if qty != gift["votes"]:
            raise PaymentError(
                f"Paket {gift['label']} berisi {gift['votes']} vote. "
                f"Sesuaikan jumlah vote atau hapus pilihan gift."
            )
    support_message = profanity_filter.censor((payload.support_message or "").strip()) or None

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

    # Serialize the short creation transaction, including the empty-result case.
    # The lock is released before any provider request.
    await session.execute(select(Event.id).where(Event.id == event.id).with_for_update(key_share=True))
    if payload.idempotency_key:
        existing_by_key = await payment_repo.get_by_idempotency_key(session, payload.idempotency_key)
        if existing_by_key:
            return _replay_or_raise(existing_by_key, payload)

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
        support_message=support_message,
        effect_code=effect_code,
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
        payment.payment_gateway = PaymentGateway.DUITKU
        payment.duitku_merchant_order_id = payment.id
        # Metode dipilih di frontend; kode dikirim apa adanya ke Duitku.
        # Tidak ada inquiry metode di sini supaya invoice tidak gagal karena
        # daftar metode Duitku tidak dapat dimuat.
        chosen_method = resolve_payment_method(payload.payment_method)
        payment.duitku_payment_method = chosen_method
        payment.duitku_payload = {'_environment': settings.DUITKU_ENVIRONMENT, '_inquiry_state': 'creating'}
        # A timeout is an unknown outcome, not proof that the provider rejected
        # the order. Make it discoverable by retries and callbacks first.
        await session.commit()
        try:
            result = await duitku_service.create_transaction(
                merchant_order_id=payment.id,
                payment_amount=payment.amount,
                payment_method=chosen_method,
                product_details=f"Voting {team.name} - {payment.votes} vote(s)",
                email=payload.supporter_email,
                customer_va_name=payload.supporter_name,
                callback_url=f"{settings.APP_BASE_URL}/api/v1/payments/duitku/callback",
                return_url=f"{settings.APP_BASE_URL}/events/{event.id}/vote/{team.id}?payment={payment.id}",
                phone_number=payload.supporter_phone,
                expiry_period=INVOICE_TTL_MINUTES,
            )
            if (not isinstance(result, dict) or result.get("statusCode") != "00"
                    or not result.get("reference")
                    or not duitku_service.valid_payment_url(result.get("paymentUrl"))):
                raise ValueError("Invalid Duitku inquiry response")
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Duitku inquiry unresolved for payment %s (method=%s, error=%s)",
                           payment.id, payment.duitku_payment_method, type(exc).__name__)
            payment = await payment_repo.get_payment(session, payment.id, for_update=True)
            payment.duitku_payload = {**(payment.duitku_payload or {}), '_inquiry_state': 'unknown'}
            await session.commit()
            payment_id = payment.id
            try:
                payment, _ = await reconcile_duitku_payment(session, payment_id)
            except PaymentError:
                # Keep the durable order even when status is temporarily unknown.
                await session.rollback()
                payment = await payment_repo.get_payment(session, payment_id)
            return payment
        payment = await payment_repo.get_payment(session, payment.id, for_update=True)
        if payment.duitku_reference and payment.duitku_reference != result['reference']:
            raise PaymentError("Referensi pembayaran tidak sesuai")
        payment.duitku_reference = result["reference"]
        payment.duitku_payload = {**(payment.duitku_payload or {}), **result, '_inquiry_state': 'ready',
                                  '_environment': 'sandbox' if duitku_service.is_sandbox_payment_url(result['paymentUrl']) else 'production'}

    await session.flush()
    return payment


async def reconcile_duitku_payment(session: AsyncSession, payment_id: str) -> tuple[Payment, bool]:
    """Explicit, rate-limited status recovery; never invent a checkout URL.

    Commit a per-invoice lease before HTTP so parallel workers cannot hammer
    Duitku and no database transaction is held open during the network call.
    """
    payment = await payment_repo.get_payment(session, payment_id, for_update=True)
    if not payment:
        raise PaymentNotFound("Invoice tidak ditemukan")
    if payment.payment_gateway != PaymentGateway.DUITKU:
        raise PaymentError("Gateway invoice tidak sesuai")
    if payment.status in (PaymentStatus.SUCCESS, PaymentStatus.CANCELED, PaymentStatus.FAILED):
        return payment, False
    payload = payment.duitku_payload or {}
    environment = payload.get('_environment')
    if environment is None and duitku_service.valid_payment_url(payload.get('paymentUrl')):
        environment = 'sandbox' if duitku_service.is_sandbox_payment_url(payload['paymentUrl']) else 'production'
    if environment != settings.DUITKU_ENVIRONMENT:
        raise PaymentUnavailable("Lingkungan pembayaran berbeda. Hubungi panitia untuk pemeriksaan invoice ini.")
    now = time.time()
    if now - payload.get('_status_checked_at', 0) < STATUS_CHECK_INTERVAL_SECONDS:
        return payment, False
    payment.duitku_payload = {**payload, '_status_checked_at': now}
    order_id = payment.duitku_merchant_order_id
    await session.commit()
    try:
        result = await duitku_service.check_transaction_status(merchant_order_id=order_id)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Duitku status unavailable for payment %s (%s)", payment_id, type(exc).__name__)
        raise PaymentUnavailable("Duitku belum merespons. Periksa status lagi setelah satu menit.") from exc

    payment = await payment_repo.get_payment(session, payment_id, for_update=True)
    if (not isinstance(result, dict) or result.get('merchantOrderId') != order_id
            or not _amount_matches(result.get('amount'), payment.amount)
            or not isinstance(result.get('reference'), str) or not 0 < len(result['reference']) <= 100
            or result.get('statusCode') not in ('00', '01', '02')
            or (payment.duitku_reference and result['reference'] != payment.duitku_reference)):
        raise PaymentError("Respons status Duitku tidak sesuai dengan invoice.")
    payment.duitku_reference = result['reference']
    payment.duitku_payload = {**(payment.duitku_payload or {}), '_transaction_status': result}
    if payment.status == PaymentStatus.SUCCESS:
        return payment, False
    if result['statusCode'] == '00':
        payment.status = PaymentStatus.SUCCESS
        payment.settled_at = payment.paid_at = datetime.now(timezone.utc)
        await session.flush()
        await process_successful_payment(session, payment)
        return payment, True
    if result['statusCode'] == '02':
        payment.status = PaymentStatus.CANCELED
    await session.flush()
    return payment, False


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
        message=("Duitku belum memberikan tautan pembayaran. Invoice ini sudah disimpan; "
                 "silakan coba lagi setelah satu menit. Jangan membuat pembayaran baru."
                 if payment.payment_gateway == PaymentGateway.DUITKU and payment.status == PaymentStatus.PENDING
                 and not duitku_service.valid_payment_url(duitku_payload.get('paymentUrl')) else ""),
        supporter_name=payment.supporter_name,
        supporter_email=payment.supporter_email,
        supporter_phone=payment.supporter_phone,
        is_anonymous=payment.is_anonymous,
        support_message=payment.support_message,
        effect_code=payment.effect_code,
        is_mock=is_mock,
        is_sandbox=(payment.payment_gateway == PaymentGateway.DUITKU
                    and (duitku_service.is_sandbox_payment_url(duitku_payload.get("paymentUrl"))
                         if duitku_service.valid_payment_url(duitku_payload.get('paymentUrl'))
                         else duitku_payload.get('_environment') == 'sandbox')),
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
        session, payload["merchantOrderId"]
    )
    if not payment:
        raise PaymentNotFound("Invoice tidak ditemukan")
    if payment.payment_gateway != PaymentGateway.DUITKU:
        raise PaymentError("Gateway invoice tidak sesuai")
    if not _amount_matches(payload["amount"], payment.amount):
        raise PaymentError("Jumlah pembayaran tidak sesuai dengan invoice")
    if payload.get("resultCode") not in ("00", "01", "02"):
        raise PaymentError("Status pembayaran tidak dikenal")
    voted_now = False
    if not payment.duitku_reference:
        payment, voted_now = await reconcile_duitku_payment(session, payment.id)
        if not payment.duitku_reference:
            raise PaymentUnavailable("Referensi Duitku belum terkonfirmasi. Kirim ulang callback nanti.")
    payment = await payment_repo.get_payment(session, payment.id, for_update=True)
    if not payment.duitku_reference or payload.get("reference") != payment.duitku_reference:
        raise PaymentError("Referensi pembayaran tidak sesuai")
    if payment.status == PaymentStatus.SUCCESS:
        return payment, voted_now
    await webhook_log_repo.create_log(
        session, payment_id=payment.id, payload=payload,
        signature=payload["signature"], is_valid=True,
    )
    payment.webhook_payload = payload
    payment.webhook_received_at = datetime.now(timezone.utc)
    # Keep the inquiry payload (checkout URL and QR) separate from callbacks.
    if payload["resultCode"] == "00":
        payment.status = PaymentStatus.SUCCESS
        payment.settled_at = payment.webhook_received_at
        payment.paid_at = payment.webhook_received_at
        await session.flush()
        await process_successful_payment(session, payment)
        await session.flush()
        return payment, True
    if payload["resultCode"] == "01":
        # Pending/processing: keep payment unresolved, do not grant votes.
        await session.flush()
        return payment, voted_now
    # result_code == "02": canceled.
    payment.status = PaymentStatus.CANCELED
    await session.flush()
    return payment, voted_now


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
