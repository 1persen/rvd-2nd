"""fix partial unique index to include all matching fields

Revision ID: 81b68844b1e1
Revises: i4d5e6f7a8b9
Create Date: 2026-11-16 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = '81b68844b1e1'
down_revision = 'i4d5e6f7a8b9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old partial unique index (only event_id, supporter_phone)
    op.drop_index(
        'ix_payments_event_phone_pending_duitku',
        table_name='payments',
        if_exists=True,
        postgresql_where=sa.text("status = 'PENDING' AND payment_gateway = 'DUITKU'")
    )
    
    # Create new partial unique index with all matching fields
    op.execute(
        "CREATE UNIQUE INDEX ix_payments_event_phone_pending_duitku "
        "ON payments (event_id, supporter_phone, team_id, qty, effect_code, support_message, duitku_payment_method) "
        "WHERE status = 'PENDING' AND payment_gateway = 'DUITKU'"
    )


def downgrade() -> None:
    # Drop new index
    op.drop_index(
        'ix_payments_event_phone_pending_duitku',
        table_name='payments',
        if_exists=True,
        postgresql_where=sa.text("status = 'PENDING' AND payment_gateway = 'DUITKU'")
    )
    # Recreate old index
    op.execute(
        "CREATE UNIQUE INDEX ix_payments_event_phone_pending_duitku "
        "ON payments (event_id, supporter_phone) "
        "WHERE status = 'PENDING' AND payment_gateway = 'DUITKU'"
    )