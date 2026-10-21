"""add education level to teams

Revision ID: 57ea6edabaa6
Revises: 9db3118321d8
Create Date: 2026-10-21 10:52:33.526335

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '57ea6edabaa6'
down_revision: Union[str, None] = '9db3118321d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('teams', sa.Column('education_level', sa.String(length=50), nullable=True))
    op.create_index('ix_teams_education_level', 'teams', ['education_level'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_teams_education_level', table_name='teams')
    op.drop_column('teams', 'education_level')
