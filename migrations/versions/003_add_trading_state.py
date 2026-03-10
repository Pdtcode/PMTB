"""add_trading_state

Revision ID: 003_add_trading_state
Revises: f63a30b29f5b
Create Date: 2026-03-10 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '003_add_trading_state'
down_revision: Union[str, Sequence[str], None] = 'f63a30b29f5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create trading_state table for halt signaling and peak portfolio value tracking."""
    op.create_table(
        'trading_state',
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('value', sa.String(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('key', name=op.f('pk_trading_state')),
    )


def downgrade() -> None:
    """Drop trading_state table."""
    op.drop_table('trading_state')
