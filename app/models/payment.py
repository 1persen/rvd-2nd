"""
Payment model — every payment event, immutable once settled.
No hard delete (business rule). vote_snapshot duplicated per transaction.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.enums.payment_channel import PaymentChannel
from app.enums.payment_gateway import PaymentGateway
from app.enums.payment_status import PaymentStatus
from app.models.mixins import TimestampMixin, UUIDMixin
from app.utils import new_id


class Payment(TimestampMixin, UUIDMixin, Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=lambda: new_id("pay")
    )
    team_id: Mapped[str] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    supporter_name: Mapped[str] = mapped_column(String(100), nullable=False)
    supporter_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    supporter_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    is_anonymous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    package_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("vote_packages.id", ondelete="RESTRICT"), nullable=True
    )
    package_code: Mapped[str] = mapped_column(String(40), nullable=False)
    package_label: Mapped[str] = mapped_column(String(80), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    votes: Mapped[int] = mapped_column(Integer, nullable=False)
    vote_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status", create_type=False), default=PaymentStatus.PENDING, nullable=False, index=True
    )
    payment_gateway: Mapped[PaymentGateway] = mapped_column(
        Enum(PaymentGateway, name="payment_gateway", create_type=False), default=PaymentGateway.MIDTRANS, nullable=False
    )
    payment_channel: Mapped[Optional[PaymentChannel]] = mapped_column(
        Enum(PaymentChannel, name="payment_channel", create_type=False), nullable=True
    )
    currency: Mapped[str] = mapped_column(String(3), default="IDR", nullable=False)
    signature_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    webhook_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    webhook_received_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    midtrans_order_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    midtrans_transaction_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    midtrans_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    duitku_merchant_order_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    duitku_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    duitku_payment_method: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    duitku_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    team: Mapped["Team"] = relationship(back_populates="payments")  # noqa: F821
    vote_logs: Mapped[list["VoteLog"]] = relationship(back_populates="payment")  # noqa: F821

    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_payment_amount_non_negative"),
        CheckConstraint("votes > 0", name="ck_payment_votes_positive"),
        CheckConstraint("qty > 0", name="ck_payment_qty_positive"),
        Index("ix_payments_event_phone_status", "event_id", "supporter_phone", "status"),
        Index("ix_payments_event_status_created", "event_id", "status", "created_at"),
    )
