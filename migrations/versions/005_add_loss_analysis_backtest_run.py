"""add_loss_analysis_backtest_run

Revision ID: 005_add_loss_analysis_backtest_run
Revises: 004_add_is_paper_column
Create Date: 2026-03-11 00:00:00.000000

Adds two new tables for Phase 7 performance tracking and learning loop:
  - loss_analyses: stores error classification results for losing trades
  - backtest_runs: stores historical backtest simulation results
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSON, UUID


# revision identifiers, used by Alembic.
revision: str = '005_add_loss_analysis_backtest_run'
down_revision: Union[str, Sequence[str], None] = '004_add_is_paper_column'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create loss_analyses and backtest_runs tables."""

    # --- loss_analyses table ---
    op.create_table(
        'loss_analyses',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            'trade_id',
            UUID(as_uuid=True),
            sa.ForeignKey('trades.id', name='fk_loss_analyses_trade_id_trades'),
            nullable=False,
        ),
        sa.Column('error_type', sa.String(), nullable=False),
        sa.Column('reasoning', sa.String(), nullable=True),
        sa.Column('classified_by', sa.String(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index('ix_loss_analyses_trade_id', 'loss_analyses', ['trade_id'])

    # --- backtest_runs table ---
    op.create_table(
        'backtest_runs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            'run_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('start_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('trade_count', sa.Integer(), nullable=False),
        sa.Column('brier_score', sa.Numeric(), nullable=True),
        sa.Column('sharpe_ratio', sa.Numeric(), nullable=True),
        sa.Column('win_rate', sa.Numeric(), nullable=True),
        sa.Column('profit_factor', sa.Numeric(), nullable=True),
        sa.Column('parameters', JSON(), nullable=False, server_default='{}'),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index('ix_backtest_runs_run_at', 'backtest_runs', ['run_at'])


def downgrade() -> None:
    """Drop loss_analyses and backtest_runs tables."""
    op.drop_index('ix_backtest_runs_run_at', table_name='backtest_runs')
    op.drop_table('backtest_runs')
    op.drop_index('ix_loss_analyses_trade_id', table_name='loss_analyses')
    op.drop_table('loss_analyses')
