"""add support message and effect code to payments

Revision ID: g2b3c4d5e6f7
Revises: d2f6a8c1e4b7

Additive only: both columns are nullable, so existing payments are unaffected.
"""
from alembic import op
import sqlalchemy as sa

revision = "g2b3c4d5e6f7"
down_revision = "d2f6a8c1e4b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("support_message", sa.String(length=150), nullable=True))
    op.add_column("payments", sa.Column("effect_code", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("payments", "effect_code")
    op.drop_column("payments", "support_message")
