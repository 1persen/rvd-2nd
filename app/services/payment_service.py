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
import copy
import hashlib
import hmac
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
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

# Rekonsiliasi Duitku: batas percobaan, backoff, dan lease (Plan 02).
RECONCILE_MAX_ATTEMPTS = 10
RECONCILE_BACKOFF_BASE = 60        # detik
RECONCILE_BACKOFF_CAP = 900        # 15 menit
RECONCILE_GRACE_AFTER_EXPIRY = 30 * 60
# Angka lease (detik), bukan timeout HTTP.
HTTP_TIMEOUT_TOTAL = 20
# Semua state rekonsiliasi disimpan di duitku_payload (D3) dengan prefix ini.
RECONCILE_STATE_PREFIX = "_"
# Status provider yang berarti order ada dan masih bisa dibayar/diproses.
_PROVIDER_PRESENT_CODES = ("00", "01")


class ReconcileVerdict(str, Enum):
    PRESENT = "PRESENT"                    # order ada di provider (pending atau sudah dibayar)
    ABSENT_CONFIRMED = "ABSENT_CONFIRMED"  # provider memastikan order tidak bisa dibayar
    INDETERMINATE = "INDETERMINATE"        # belum ada kepastian


@dataclass
class ReconcileResult:
    payment: Payment
    voted_now: bool
    verdict: ReconcileVerdict


def reconcile_delay(attempts: int) -> int:
    """60, 120, 240, 480, 900, 900, ..."""
    return min(RECONCILE_BACKOFF_BASE * (2 ** max(attempts - 1, 0)), RECONCILE_BACKOFF_CAP)

# Status Midtrans yang dianggap sukses â†’ vote diberikan.
_SUCCESS_TRANSACTIONS = {"settlement", "capture"}
# Status yang final gagal (tidak akan berubah lagi).
_FAILED_TRANSACTIONS = {"deny", "cancel", "expire", "failure"}


class PaymentError(Exception):
    """Raised for expected business errors inside payment flow."""


class PaymentNotFound(PaymentError):
    """The referenced payment does not exist."""


class PaymentUnavailable(PaymentError):
    """The payment gateway is unconfigured or temporarily unavailable."""


def status_token(payment_id: str) -> str:
    """Token baca-status yang deterministik dan tidak dapat ditebak.

    Diturunkan dari SECRET_KEY (bukan JWT_SECRET_KEY), tidak disimpan di DB, dan
    tidak punya masa berlaku: token hidup selama payment_id hidup.
    """
    message = f"payment-status:{payment_id}".encode("utf-8")
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), message, hashlib.sha256).hexdigest()[:32]


def verify_status_token(payment_id: str, token: str | None) -> bool:
    if not token:
        return False
    return hmac.compare_digest(status_token(payment_id), token)


def _method_or_default(code: Optional[str]) -> Optional[str]:
    """Normalisasi tanpa melempar: dipakai saat membandingkan request lama."""
    if not code:
        return PAYMENT_METHOD_CODES[0]
    return code if code in PAYMENT_METHOD_CODES else code


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
    # D7: metode pembayaran bagian dari identitas idempotensi (invoice Duitku).
    # Invoice Midtrans legacy (metode NULL) tidak membawa identitas metode.
    existing_method = existing.duitku_payment_method
    requested_method = _method_or_default(payload.payment_method)
    method_matches = (
        existing_method is None
        or existing.payment_gateway != PaymentGateway.DUITKU
        or existing_method == requested_method
    )
    if not same_request or not method_matches:
        raise PaymentError("Idempotency-Key sudah dipakai untuk permintaan lain")
    # D8: invoice Duitku yang hasilnya belum pasti memblokir retry. Mengembalikan
    # invoice lama sebagai replay akan menyembunyikan bahwa statusnya unresolved.
    # Namun replay berbasis idempotency key harus tetap mengembalikan invoice yang sama
    # agar klien bisa memeriksa statusnya.
    if (
        existing.payment_gateway == PaymentGateway.DUITKU
        and existing.status == PaymentStatus.PENDING
        and (existing.duitku_payload or {}).get("_inquiry_state") in ("unknown", "creating")
        and not existing.idempotency_key
    ):
        raise PaymentError(
            "Pesanan sebelumnya masih kami proses. Jangan membuat pembayaran "
            "baru. Buka status pesanan untuk memeriksa atau melanjutkan."
        )
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
    existing = await payment_repo.get_pending_by_phone_and_event_for_update(
        session, event.id, payload.supporter_phone
    )
    if existing:
        if existing.payment_gateway == PaymentGateway.MIDTRANS:
            # Perilaku legacy tidak diubah.
            existing.status = PaymentStatus.CANCELED
            existing.idempotency_key = None
            await session.flush()
        else:
            state = (existing.duitku_payload or {}).get("_inquiry_state")
            if state == "rejected":
                existing.status = PaymentStatus.CANCELED
                existing.idempotency_key = None
                await session.flush()
            else:
                raise PaymentError(
                    "Pesanan sebelumnya masih kami proses. Jangan membuat pembayaran "
                    "baru. Buka status pesanan untuk memeriksa atau melanjutkan."
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
                return_url=(
                    f"{settings.APP_BASE_URL}/events/{event.id}/vote/{team.id}"
                    f"?payment={payment.id}&t={status_token(payment.id)}"
                ),
                phone_number=payload.supporter_phone,
                expiry_period=INVOICE_TTL_MINUTES,
            )
        except duitku_service.DuitkuRejected as exc:
            logger.warning(
                "Duitku inquiry rejected for payment %s (method=%s, status=%s, reason=%s)",
                payment.id, payment.duitku_payment_method, exc.status_code, exc.reason[:200],
            )
            payment = await payment_repo.get_payment(session, payment.id, for_update=True)
            if payment.status != PaymentStatus.PENDING:
                # Callback sudah menang duluan. Jangan menurunkan status (D11).
                await session.rollback()
                raise PaymentError(
                    "Pembayaran ini sudah diproses. Periksa status pesanan."
                ) from exc
            payment.status = PaymentStatus.FAILED
            payment.idempotency_key = None
            payment.duitku_payload = {
                **(payment.duitku_payload or {}),
                "_inquiry_state": "rejected",
                "_last_error": (exc.reason or "rejected")[:200],
                "_last_status_code": exc.status_code,
            }
            await session.commit()
            raise PaymentError(
                "Metode pembayaran ini menolak transaksi. Silakan pilih metode lain: "
                "QRIS, ShopeePay, atau DANA."
            ) from exc

        except (duitku_service.DuitkuUncertain, httpx.HTTPError) as exc:
            logger.warning(
                "Duitku inquiry uncertain for payment %s (method=%s, kind=%s)",
                payment.id, payment.duitku_payment_method, getattr(exc, "kind", type(exc).__name__),
            )
            payment = await payment_repo.get_payment(session, payment.id, for_update=True)
            if payment.status != PaymentStatus.PENDING:
                # Callback sudah menang; jangan menimpa hasilnya dengan unknown.
                await session.rollback()
                return payment
            payment.duitku_payload = {
                **(payment.duitku_payload or {}),
                "_inquiry_state": "unknown",
                "_last_error": getattr(exc, "kind", type(exc).__name__),
                "_attempts": 0,
                "_next_check_at": time.time() + 60,
            }
            await session.commit()
            return payment
        payment = await payment_repo.get_payment(session, payment.id, for_update=True)
        # Klasifikasi status provider lebih dulu (plan 01 Â§4). Mock/inquiry yang
        # mengembalikan dict tanpa melempar exception tetap harus dipetakan:
        # statusCode selain "00" adalah penolakan definitif, bukan "tidak pasti".
        result_code = result.get("statusCode") if isinstance(result, dict) else None
        if isinstance(result, dict) and result_code and result_code != "00":
            payment.status = PaymentStatus.FAILED
            payment.idempotency_key = None
            payment.duitku_payload = {
                **(payment.duitku_payload or {}),
                "_inquiry_state": "rejected",
                "_last_error": "rejected",
                "_last_status_code": str(result_code),
            }
            await session.commit()
            raise PaymentError(
                "Metode pembayaran ini menolak transaksi. Silakan pilih metode lain: "
                "QRIS, ShopeePay, atau DANA."
            )
        reference = result.get("reference") if isinstance(result, dict) else None
        payment_url = result.get("paymentUrl") if isinstance(result, dict) else None
        if not isinstance(reference, str) or not reference or not duitku_service.valid_payment_url(payment_url):
            # Sukses cacat (tanpa reference atau tanpa tautan bayar yang sah):
            # perlakukan sebagai tidak pasti, jangan KeyError -> 500 (D4, D5).
            payment.duitku_payload = {
                **(payment.duitku_payload or {}),
                "_inquiry_state": "unknown",
                "_last_error": "malformed_success",
                "_attempts": 0,
                "_next_check_at": time.time() + 60,
            }
            await session.commit()
            return payment
        if payment.duitku_reference and payment.duitku_reference != reference:
            raise PaymentError("Referensi pembayaran tidak sesuai")
        payment.duitku_reference = reference
        payment.duitku_payload = {**(payment.duitku_payload or {}), **result, '_inquiry_state': 'ready',
                                  '_environment': 'sandbox' if duitku_service.is_sandbox_payment_url(result['paymentUrl']) else 'production'}

    await session.flush()
    return payment


def _reconcile_mark_attempt(payload: dict, attempts: int) -> None:
    """Catat satu percobaan provider: jumlah, waktu, dan jadwal berikutnya."""
    now = time.time()
    payload["_attempts"] = attempts + 1
    payload["_next_check_at"] = now + reconcile_delay(attempts + 1)
    payload["_last_checked_at"] = now


def _reconcile_needs_review(payment: Payment, attempts: int) -> bool:
    """True bila batas percobaan atau masa tenggang setelah kedaluwarsa tercapai."""
    expired_deadline = (
        payment.expires_at.timestamp() if payment.expires_at else time.time()
    ) + RECONCILE_GRACE_AFTER_EXPIRY
    return attempts >= RECONCILE_MAX_ATTEMPTS or time.time() > expired_deadline


def _reconcile_lease_free(payment: Payment, payload: dict) -> bool:
    """Lease boleh diambil bila kosong atau sudah kedaluwarsa.

    Owner bersifat unik per proses+panggilan (bukan payment.id): memakai payment.id
    membuat semua worker dianggap pemilik yang sama dan lease tidak pernah
    memblokir apa pun.
    """
    until = payload.get("_lease_until")
    if until is None or until <= time.time():
        return True
    return False


def _new_lease_owner() -> str:
    """Penanda unik per proses dan per klaim lease."""
    return f"{os.getpid()}:{uuid.uuid4().hex[:12]}"


async def _settle_once(session: AsyncSession, payment: Payment) -> bool:
    """Beri vote tepat satu kali untuk satu payment (D14).

    Dua jalur (reconcile manual dan callback) bisa berjalan bersamaan: masing-
    masing membaca status PENDING sebelum salah satunya commit. Unique index
    ``ix_vote_logs_payment_id`` adalah penjaga terakhir; tabrakan di dalamnya
    berarti payment ini sudah diselesaikan jalur lain, jadi hasilnya idempoten
    (bukan kegagalan, bukan vote kedua).

    Mengembalikan True bila vote benar-benar diberikan oleh pemanggilan ini.
    """
    savepoint = await session.begin_nested()
    try:
        await process_successful_payment(session, payment)
        await savepoint.commit()
        return True
    except IntegrityError as exc:
        await savepoint.rollback()
        if "ix_vote_logs_payment_id" not in str(exc):
            raise
        logger.info(
            "Vote untuk payment %s sudah dicatat jalur lain; settle dianggap selesai",
            payment.id,
        )
        return False

async def reconcile_duitku_payment(
    session: AsyncSession, payment_id: str
) -> ReconcileResult:
    """Explicit status recovery driven by the provider, never by local time.

    A per-invoice lease (claimed under the row lock, then released before the
    HTTP call) keeps parallel workers from hammering Duitku; the lease â€” not
    the old 60s throttle â€” decides whether the provider may be called.
    Ambiguous answers are INDETERMINATE: unknown is never turned into a failure.
    """
    payment = await payment_repo.get_payment(session, payment_id, for_update=True)
    if not payment:
        raise PaymentNotFound("Invoice tidak ditemukan")
    if payment.payment_gateway != PaymentGateway.DUITKU:
        raise PaymentError("Gateway invoice tidak sesuai")
    if payment.status == PaymentStatus.SUCCESS:
        return ReconcileResult(payment, False, ReconcileVerdict.PRESENT)
    if payment.duitku_reference and payment.status in (PaymentStatus.CANCELED, PaymentStatus.FAILED):
        return ReconcileResult(payment, False, ReconcileVerdict.ABSENT_CONFIRMED)
    payload = payment.duitku_payload or {}
    environment = payload.get('_environment')
    if environment is None and duitku_service.valid_payment_url(payload.get('paymentUrl')):
        environment = 'sandbox' if duitku_service.is_sandbox_payment_url(payload['paymentUrl']) else 'production'
    if environment != settings.DUITKU_ENVIRONMENT:
        raise PaymentUnavailable("Lingkungan pembayaran berbeda. Hubungi panitia untuk pemeriksaan invoice ini.")

    now = time.time()
    if not _reconcile_lease_free(payment, payload):
        # Worker lain sedang memeriksa invoice ini: tidak memanggil provider.
        payment.duitku_payload = {**payload, '_status_checked_at': now}
        await session.flush()
        return ReconcileResult(payment, False, ReconcileVerdict.INDETERMINATE)

    payload = {**payload, '_status_checked_at': now}
    payload['_lease_owner'] = _new_lease_owner()
    payload['_lease_until'] = now + 2 * HTTP_TIMEOUT_TOTAL
    payload['_lease_acquired_at'] = now
    payment.duitku_payload = payload
    order_id = payment.duitku_merchant_order_id
    attempts = int(payload.get('_attempts') or 0)
    await session.commit()

    # Panggilan provider di luar transaksi. Semua kegagalan ambigu adalah
    # INDETERMINATE, bukan penolakan dan bukan kegagalan transaksi.
    result = None
    try:
        result = await duitku_service.check_transaction_status(merchant_order_id=order_id)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Duitku status unavailable for payment %s (%s)", payment_id, type(exc).__name__)

    payment = await payment_repo.get_payment(session, payment_id, for_update=True)
    if not payment:
        raise PaymentNotFound("Invoice tidak ditemukan")
    payload = dict(payment.duitku_payload or {})
    _reconcile_mark_attempt(payload, attempts)
    attempts += 1

    def _release() -> None:
        for key in ('_lease_owner', '_lease_until', '_lease_acquired_at'):
            payload.pop(key, None)

    # Validasi identitas/invoice: kegagalan di sini adalah manipulasi, bukan verdict.
    # statusCode yang tak dikenal bukan alasan menolak â€” itu INDETERMINATE.
    if result is not None:
        mismatch = (
            not isinstance(result, dict)
            or result.get('merchantOrderId') != order_id
            or not _amount_matches(result.get('amount'), payment.amount)
            or not isinstance(result.get('reference'), str) or not 0 < len(result['reference']) <= 100
            or bool(payment.duitku_reference and result['reference'] != payment.duitku_reference)
        )
        if mismatch:
            _release()
            payload['_last_verdict'] = ReconcileVerdict.INDETERMINATE.value
            payment.duitku_payload = payload
            await session.flush()
            raise PaymentError("Respons status Duitku tidak sesuai dengan invoice.")

    status_code = result.get('statusCode') if result is not None else None
    # G0.5 belum terjawab: "order tidak ditemukan" tidak bisa dibedakan dari
    # propagasi, jadi TIDAK PERNAH menjadi ABSENT_CONFIRMED (degradasi index Â§3).
    if status_code in _PROVIDER_PRESENT_CODES:
        payment.duitku_reference = result['reference']
        payload['_transaction_status'] = result
        payload['_last_verdict'] = ReconcileVerdict.PRESENT.value
        payload.pop('_needs_manual_review', None)
        _release()
        voted_now = False
        if status_code == '00':
            payment.status = PaymentStatus.SUCCESS
            payment.settled_at = payment.paid_at = datetime.now(timezone.utc)
            await session.flush()
            voted_now = await _settle_once(session, payment)
        payment.duitku_payload = payload
        await session.flush()
        return ReconcileResult(payment, voted_now, ReconcileVerdict.PRESENT)

    if status_code == '02':
        # Satu-satunya sumber ABSENT_CONFIRMED saat G0.2/G0.3/G0.5 belum terjawab.
        payment.status = PaymentStatus.CANCELED
        payment.duitku_reference = result['reference']
        payload['_transaction_status'] = result
        payload['_last_verdict'] = ReconcileVerdict.ABSENT_CONFIRMED.value
        payload.pop('_needs_manual_review', None)
        _release()
        payment.duitku_payload = payload
        await session.flush()
        return ReconcileResult(payment, False, ReconcileVerdict.ABSENT_CONFIRMED)

    # Timeout, 5xx, body rusak, status tak dikenal, atau "tidak ditemukan".
    # Status tidak pernah diubah; _needs_manual_review tidak mengubah status.
    payload['_last_verdict'] = ReconcileVerdict.INDETERMINATE.value
    if _reconcile_needs_review(payment, attempts):
        payload['_needs_manual_review'] = True
    _release()
    payment.duitku_payload = payload
    await session.flush()
    return ReconcileResult(payment, False, ReconcileVerdict.INDETERMINATE)


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
        payment_url=(
            duitku_payload.get("paymentUrl")
            if payment.status == PaymentStatus.PENDING
            and duitku_service.valid_payment_url(duitku_payload.get("paymentUrl"))
            else None
        ),
        expires_at=payment.expires_at,
        webhook_received_at=payment.webhook_received_at,
        settled_at=payment.settled_at,
        created_at=payment.created_at,
        message=(
            "Pesanan kamu sedang kami periksa. Jangan membuat pembayaran baru "
            "sampai statusnya dipastikan."
            if payment.payment_gateway == PaymentGateway.DUITKU
            and payment.status == PaymentStatus.PENDING
            and not duitku_service.valid_payment_url(duitku_payload.get("paymentUrl"))
            else ""
        ),
        inquiry_state=duitku_payload.get("_inquiry_state"),
        needs_manual_review=bool(duitku_payload.get("_needs_manual_review")),
        status_token=status_token(payment.id),
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


async def process_duitku_callback(
    session: AsyncSession, payload: dict, inbox_id: Optional[str] = None
) -> tuple[Payment, bool]:
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
        result = await reconcile_duitku_payment(session, payment.id)
        payment, voted_now = result.payment, result.voted_now
        if not payment.duitku_reference:
            raise PaymentUnavailable("Referensi Duitku belum terkonfirmasi. Kirim ulang callback nanti.")
    payment = await payment_repo.get_payment(session, payment.id, for_update=True)
    if not payment.duitku_reference or payload.get("reference") != payment.duitku_reference:
        raise PaymentError("Referensi pembayaran tidak sesuai")
    if payment.status == PaymentStatus.SUCCESS:
        return payment, voted_now
    if inbox_id:
        # Callback already stored as inbox when the signature was verified.
        await webhook_log_repo.mark_processed(session, inbox_id, "processed")
        # Inbox dari router belum tahu payment-nya; lengkapi agar bisa ditelusuri.
        await webhook_log_repo.attach_payment(session, inbox_id, payment.id)
    else:
        # Pemanggil lama (mis. test service langsung) tidak membuat inbox.
        await webhook_log_repo.create_log(
            session, payment_id=payment.id, payload=payload,
            signature=payload["signature"], is_valid=True, provider="duitku",
            merchant_order_id=payload["merchantOrderId"],
        )
    payment.webhook_payload = payload
    payment.webhook_received_at = datetime.now(timezone.utc)
    # Keep the inquiry payload (checkout URL and QR) separate from callbacks.
    if payload["resultCode"] == "00":
        if payment.status == PaymentStatus.SUCCESS:
            # Reconcile di atas bisa saja sudah menyelesaikan pembayaran ini.
            # Vote hanya boleh diberikan sekali (D14): jangan settle ulang.
            await session.flush()
            return payment, voted_now
        payment.status = PaymentStatus.SUCCESS
        payment.settled_at = payment.webhook_received_at
        payment.paid_at = payment.webhook_received_at
        await session.flush()
        await _settle_once(session, payment)
        await session.flush()
        return payment, True
    if payload["resultCode"] == "01":
        # Pending/processing: keep payment unresolved, do not grant votes.
        # G0.1 belum terjawab: pertahankan no-op.
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

    # Idempotency guard â€” identical to process_webhook (line ~230).
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
