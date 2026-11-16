"""add creating_started_at and partial unique index for duitku pending

Revision ID: i4d5e6f7a8b9
Revises: h3c4d5e6f7a8
Create Date: 2026-11-16 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

revision = "i4d5e6f7a8b9"
down_revision = "h3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('payments')]

    # Add creating_started_at column if not exists
    if 'creating_started_at' not in columns:
        op.add_column(
            'payments',
            sa.Column('creating_started_at', sa.DateTime(timezone=True), nullable=True)
        )

    # Create partial unique index: one PENDING DUITKU invoice per (event, phone, team, qty, effect, message, method)
    # This prevents duplicate pending invoices for IDENTICAL data at the database level,
    # while allowing different data (different team, qty, etc.) to create new invoices.
    # Use autocommit_block because the DUITKU enum value was added in a previous migration
    # and must be committed before it can be referenced in a WHERE clause (PostgreSQL restriction).
    indexes = {index['name'] for index in inspector.get_indexes('payments')}
    if 'ix_payments_event_phone_pending_duitku' not in indexes:
        with op.get_context().autocommit_block():
            op.execute(
                "CREATE UNIQUE INDEX ix_payments_event_phone_pending_duitku "
                "ON payments (event_id, supporter_phone, team_id, qty, effect_code, support_message, duitku_payment_method) "
                "WHERE status = 'PENDING' AND payment_gateway = 'DUITKU'"
            )


def downgrade() -> None:
    # Drop partial unique index
    op.drop_index(
        'ix_payments_event_phone_pending_duitku',
        table_name='payments',
        if_exists=True,
        postgresql_where=sa.text("status = 'PENDING' AND payment_gateway = 'DUITKU'")
    )
    # Drop creating_started_at column
    op.drop_column('payments', 'creating_started_at')
