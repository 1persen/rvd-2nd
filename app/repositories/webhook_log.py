"""
Webhook log repository — immutable records of incoming webhook payloads.
"""
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook_log import WebhookLog
from app.utils import new_id


async def create_log(
    session: AsyncSession,
    *,
    payment_id: Optional[str],
    payload: dict,
    signature: Optional[str] = None,
    is_valid: bool = True,
    provider: str = "midtrans",
    merchant_order_id: Optional[str] = None,
) -> WebhookLog:
    log = WebhookLog(
        id=new_id("wlog"),
        payment_id=payment_id,
        merchant_order_id=merchant_order_id,
        provider=provider,
        payload=payload,
        signature=signature,
        is_valid=is_valid,
    )
    session.add(log)
    await session.flush()
    return log


async def mark_processed(session: AsyncSession, log_id: str, status: str) -> None:
    """Set processed_at dan status pada satu baris inbox."""
    await session.execute(
        update(WebhookLog)
        .where(WebhookLog.id == log_id)
        .values(processed_at=datetime.now(timezone.utc), status=status)
    )


async def attach_payment(session: AsyncSession, log_id: str, payment_id: str) -> None:
    """Hubungkan baris inbox ke payment setelah pencocokan berhasil."""
    await session.execute(
        update(WebhookLog)
        .where(WebhookLog.id == log_id)
        .values(payment_id=payment_id)
    )


async def list_by_payment(
    session: AsyncSession, payment_id: str
) -> Sequence[WebhookLog]:
    stmt = (
        select(WebhookLog)
        .where(WebhookLog.payment_id == payment_id, WebhookLog.deleted_at.is_(None))
        .order_by(WebhookLog.received_at.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()
