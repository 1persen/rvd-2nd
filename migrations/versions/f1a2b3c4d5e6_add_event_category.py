"""add event category

Revision ID: f1a2b3c4d5e6
Revises: e53e8b224a54
"""
from alembic import op
import sqlalchemy as sa

revision = "f1a2b3c4d5e6"
down_revision = "e53e8b224a54"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("events", sa.Column("category", sa.String(length=20), nullable=True))
    op.create_index("ix_events_category", "events", ["category"], unique=False)

def downgrade() -> None:
    op.drop_index("ix_events_category", table_name="events")
    op.drop_column("events", "category")
