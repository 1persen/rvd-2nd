"""add duitku fields to payments

Revision ID: c0d1e2f3a4b5
Revises: b7c8d9e0f1a2
Create Date: 2026-10-15 00:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c0d1e2f3a4b5'
down_revision: Union[str, None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # Add columns for Duitku integration (idempotent)
    conn = bind
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('payments')]
    if 'duitku_merchant_order_id' not in columns:
        op.add_column('payments', sa.Column('duitku_merchant_order_id', sa.String(length=100), nullable=True))
    if 'duitku_reference' not in columns:
        op.add_column('payments', sa.Column('duitku_reference', sa.String(length=100), nullable=True))
    if 'duitku_payment_method' not in columns:
        op.add_column('payments', sa.Column('duitku_payment_method', sa.String(length=50), nullable=True))
    if 'duitku_payload' not in columns:
        op.add_column('payments', sa.Column('duitku_payload', sa.JSON(), nullable=True))
    # Add index if not exists
    try:
        op.create_index('ix_payments_duitku_merchant_order_id', 'payments', ['duitku_merchant_order_id'], unique=False)
    except Exception:
        pass


def downgrade() -> None:
    try:
        op.drop_index('ix_payments_duitku_merchant_order_id', table_name='payments')
    except Exception:
        pass
    op.drop_column('payments', 'duitku_payload')
    op.drop_column('payments', 'duitku_payment_method')
    op.drop_column('payments', 'duitku_reference')
    op.drop_column('payments', 'duitku_merchant_order_id')
