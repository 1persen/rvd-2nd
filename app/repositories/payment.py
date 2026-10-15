"""
Payment repository — all DB queries for payments live here.
Transaksi pembayaran tidak boleh di-hard delete (business rule).
"""
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import Payment
from app.enums.payment_status import PaymentStatus


async def get_payment(
    session: AsyncSession, payment_id: str, for_update: bool = False
) -> Optional[Payment]:
    return await session.get(Payment, payment_id, with_for_update=for_update or None)


async def get_by_midtrans_order_id(
    session: AsyncSession, order_id: str, for_update: bool = False
) -> Optional[Payment]:
    stmt = select(Payment).where(Payment.midtrans_order_id == order_id)
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_duitku_merchant_order_id(
    session: AsyncSession, merchant_order_id: str, for_update: bool = False
) -> Optional[Payment]:
    stmt = select(Payment).where(Payment.duitku_merchant_order_id == merchant_order_id)
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_idempotency_key(session: AsyncSession, key: str) -> Optional[Payment]:
    stmt = select(Payment).where(Payment.idempotency_key == key)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_payments(
    session: AsyncSession,
    event_id: Optional[str] = None,
    status: Optional[PaymentStatus] = None,
    limit: int = 100,
) -> Sequence[Payment]:
    stmt = select(Payment).order_by(Payment.created_at.desc()).limit(limit)
    if event_id:
        stmt = stmt.where(Payment.event_id == event_id)
    if status:
        stmt = stmt.where(Payment.status == status)
    result = await session.execute(stmt)
    return result.scalars().all()


async def list_successful_payments(
    session: AsyncSession,
    event_id: Optional[str] = None,
    limit: int = 5000,
) -> Sequence[Payment]:
    stmt = (
        select(Payment)
        .where(Payment.status.in_([PaymentStatus.SUCCESS, PaymentStatus.SETTLED]))
        .order_by(Payment.created_at.desc())
        .limit(limit)
    )
    if event_id:
        stmt = stmt.where(Payment.event_id == event_id)
    result = await session.execute(stmt)
    return result.scalars().all()


_SUCCESS_STATUSES = (PaymentStatus.SUCCESS, PaymentStatus.SETTLED)


async def list_donations(session: AsyncSession, event_id: str, limit: int = 5000):
    """Successful payments of one event as light rows (no JSON payload columns):
    (supporter_name, team_id, amount, votes, created_at), newest first."""
    stmt = (
        select(
            Payment.supporter_name,
            Payment.team_id,
            Payment.amount,
            Payment.votes,
            Payment.created_at,
        )
        .where(Payment.event_id == event_id, Payment.status.in_(_SUCCESS_STATUSES))
        .order_by(Payment.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return result.all()


async def status_summary(session: AsyncSession, event_id: str) -> dict[PaymentStatus, tuple[int, int]]:
    """status -> (count, total amount) for one event, aggregated in SQL."""
    stmt = (
        select(Payment.status, func.count(Payment.id), func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.event_id == event_id)
        .group_by(Payment.status)
    )
    result = await session.execute(stmt)
    return {status: (count, int(amount)) for status, count, amount in result.all()}


async def count_donors_by_event(session: AsyncSession, event_ids: Sequence[str]) -> dict[str, int]:
    """event_id -> distinct supporter names with a successful payment, one query."""
    if not event_ids:
        return {}
    stmt = (
        select(Payment.event_id, func.count(func.distinct(Payment.supporter_name)))
        .where(
            Payment.event_id.in_(event_ids),
            Payment.status.in_(_SUCCESS_STATUSES),
            Payment.supporter_name != "",
        )
        .group_by(Payment.event_id)
    )
    result = await session.execute(stmt)
    return dict(result.all())


async def list_by_status(
    session: AsyncSession, status: PaymentStatus, limit: int = 100
) -> Sequence[Payment]:
    stmt = (
        select(Payment)
        .where(Payment.status == status)
        .order_by(Payment.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def mark_status(session: AsyncSession, payment: Payment, status: PaymentStatus) -> None:
    payment.status = status
    if status == PaymentStatus.SUCCESS:
        payment.paid_at = datetime.utcnow()
    await session.flush()


async def get_active_pending_by_phone_and_event(
    session: AsyncSession, event_id: str, phone: str
) -> Optional[Payment]:
    """Return PENDING terbaru yang masih aktif (expires_at > now)."""
    stmt = (
        select(Payment)
        .where(Payment.event_id == event_id)
        .where(Payment.supporter_phone == phone)
        .where(Payment.status == PaymentStatus.PENDING)
        .where(Payment.expires_at > datetime.now(timezone.utc))
        .order_by(Payment.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_active_pending_by_phone_and_event_for_update(
    session: AsyncSession, event_id: str, phone: str
) -> Optional[Payment]:
    """Return PENDING terbaru yang masih aktif, dengan pessimistic lock."""
    stmt = (
        select(Payment)
        .where(Payment.event_id == event_id)
        .where(Payment.supporter_phone == phone)
        .where(Payment.status == PaymentStatus.PENDING)
        .where(Payment.expires_at > datetime.now(timezone.utc))
        .order_by(Payment.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def expire_if_pending(session: AsyncSession, payment_id: str) -> None:
    """PENDING -> EXPIRED only if still PENDING in the database, so a webhook
    that settled the payment meanwhile is never overwritten."""
    await session.execute(
        update(Payment)
        .where(Payment.id == payment_id, Payment.status == PaymentStatus.PENDING)
        .values(status=PaymentStatus.EXPIRED)
        .execution_options(synchronize_session=False)
    )


async def expire_old_pendings_by_phone_and_event(
    session: AsyncSession, event_id: str, phone: str
) -> int:
    """Expire semua PENDING yang sudah kedaluwarsa untuk phone+event.
    Return jumlah baris yang terupdate."""
    stmt = (
        update(Payment)
        .where(Payment.event_id == event_id)
        .where(Payment.supporter_phone == phone)
        .where(Payment.status == PaymentStatus.PENDING)
        .where(Payment.expires_at <= datetime.now(timezone.utc))
        .values(status=PaymentStatus.EXPIRED)
    )
    result = await session.execute(stmt)
    return result.rowcount
