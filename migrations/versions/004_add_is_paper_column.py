"""add_is_paper_column

Revision ID: 004_add_is_paper_column
Revises: 003_add_trading_state
Create Date: 2026-03-10 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '004_add_is_paper_column'
down_revision: Union[str, Sequence[str], None] = '003_add_trading_state'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add is_paper boolean column to orders table (default False, NOT NULL)."""
    op.add_column(
        'orders',
        sa.Column('is_paper', sa.Boolean(), nullable=False, server_default='false'),
    )


def downgrade() -> None:
    """Remove is_paper column from orders table."""
    op.drop_column('orders', 'is_paper')
