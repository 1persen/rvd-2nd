"""
Webhook log model — immutable records of incoming webhook payloads.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDMixin
from app.utils import new_id


class WebhookLog(TimestampMixin, UUIDMixin, Base):
    __tablename__ = "webhook_logs"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=lambda: new_id("wlog")
    )
    # Nullable: a valid callback can arrive before its payment is matched (inbox).
    payment_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("payments.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    merchant_order_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), default="midtrans", nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    signature: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
