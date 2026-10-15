"""add Duitku value to payment_gateway enum

The initial schema migration created payment_gateway as
("MIDTRANS", "MANUAL", "TRANSFER"). The Duitku integration sets
PaymentGateway.DUITKU on payments, so the enum must carry that value too.
Postgres refuses to insert "DUITKU" into a type that does not declare it.

Revision ID: e7f6a1b2c3d4
Revises: c0d1e2f3a4b5
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e7f6a1b2c3d4"
down_revision: Union[str, None] = "c0d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # Idempotent: only add the value if it is not already present.
    existing = bind.execute(
        sa.text(
            "SELECT e.enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = 'payment_gateway' AND e.enumlabel = 'DUITKU'"
        )
    ).fetchone()
    if not existing:
        op.execute("ALTER TYPE payment_gateway ADD VALUE 'DUITKU'")


def downgrade() -> None:
    # Removing an in-use enum value is unsafe on Postgres; leave the value.
    pass