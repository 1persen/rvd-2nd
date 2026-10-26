"""add event presentation (invite template + theme config)

Revision ID: d2f6a8c1e4b7
Revises: f1a2b3c4d5e6

Additive only: every existing event keeps rendering because the server defaults
give the classic template and an empty theme config. The JSONB default is a
server_default too, so rows written by older app code stay valid during a
rolling deploy. Downgrade drops both columns and therefore the theme choices;
uploaded Storage objects are not touched by a downgrade.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d2f6a8c1e4b7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column(
            "template_key",
            sa.String(length=20),
            nullable=False,
            server_default="classic",
        ),
    )
    op.add_column(
        "events",
        sa.Column(
            "theme_config",
            JSON_TYPE,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("events", "theme_config")
    op.drop_column("events", "template_key")
