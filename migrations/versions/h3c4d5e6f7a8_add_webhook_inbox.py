"""webhook inbox: callback tanpa payment_id

Revision ID: h3c4d5e6f7a8
Revises: g2b3c4d5e6f7

Additive only: payment_id becomes nullable so a valid callback can be stored
before its payment is matched, and merchant_order_id (nullable + index) keeps
the provider order id for that matching. No unique constraint: the same callback
may legitimately be delivered repeatedly (pending then success).
"""
import sqlalchemy as sa
from alembic import op

revision = "h3c4d5e6f7a8"
down_revision = "g2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "webhook_logs", "payment_id",
        existing_type=sa.String(length=40), nullable=True,
    )
    op.add_column(
        "webhook_logs",
        sa.Column("merchant_order_id", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_webhook_logs_merchant_order_id", "webhook_logs", ["merchant_order_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_logs_merchant_order_id", table_name="webhook_logs")
    op.drop_column("webhook_logs", "merchant_order_id")
    # Gagal bila sudah ada baris dengan payment_id NULL. Itu disengaja.
    op.alter_column(
        "webhook_logs", "payment_id",
        existing_type=sa.String(length=40), nullable=False,
    )
