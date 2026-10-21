"""merge heads for education level

Revision ID: e53e8b224a54
Revises: 57ea6edabaa6, e7f6a1b2c3d4
Create Date: 2026-10-21 13:19:22.018428

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e53e8b224a54'
down_revision: Union[str, None] = ('57ea6edabaa6', 'e7f6a1b2c3d4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
