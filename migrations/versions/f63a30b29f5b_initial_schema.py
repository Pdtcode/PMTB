"""initial_schema

Revision ID: f63a30b29f5b
Revises:
Create Date: 2026-03-09 23:43:51.051224

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f63a30b29f5b'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all PMTB tables from scratch."""

    # --- markets ---
    op.create_table(
        'markets',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('ticker', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='active'),
        sa.Column('close_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_markets')),
        sa.UniqueConstraint('ticker', name=op.f('uq_markets_ticker')),
    )
    op.create_index(op.f('ix_markets_ticker'), 'markets', ['ticker'], unique=False)
    op.create_index(op.f('ix_markets_status'), 'markets', ['status'], unique=False)
    op.create_index(op.f('ix_markets_close_time'), 'markets', ['close_time'], unique=False)

    # --- orders ---
    op.create_table(
        'orders',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('market_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('side', sa.String(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('price', sa.Numeric(), nullable=False),
        sa.Column('order_type', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='pending'),
        sa.Column('kalshi_order_id', sa.String(), nullable=True),
        sa.Column('fill_price', sa.Numeric(), nullable=True),
        sa.Column('filled_quantity', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('placed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['market_id'], ['markets.id'],
            name=op.f('fk_orders_market_id_markets')
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_orders')),
        sa.UniqueConstraint('kalshi_order_id', name=op.f('uq_orders_kalshi_order_id')),
    )
    op.create_index(op.f('ix_orders_market_id'), 'orders', ['market_id'], unique=False)
    op.create_index(op.f('ix_orders_status'), 'orders', ['status'], unique=False)
    op.create_index(op.f('ix_orders_kalshi_order_id'), 'orders', ['kalshi_order_id'], unique=False)

    # --- positions ---
    op.create_table(
        'positions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('market_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('side', sa.String(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('avg_price', sa.Numeric(), nullable=False),
        sa.Column('current_value', sa.Numeric(), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='open'),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['market_id'], ['markets.id'],
            name=op.f('fk_positions_market_id_markets')
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_positions')),
        sa.UniqueConstraint('market_id', name=op.f('uq_positions_market_id')),
    )

    # --- trades ---
    op.create_table(
        'trades',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('order_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('market_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('side', sa.String(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('price', sa.Numeric(), nullable=False),
        sa.Column('pnl', sa.Numeric(), nullable=True),
        sa.Column('resolved_outcome', sa.String(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['market_id'], ['markets.id'],
            name=op.f('fk_trades_market_id_markets')
        ),
        sa.ForeignKeyConstraint(
            ['order_id'], ['orders.id'],
            name=op.f('fk_trades_order_id_orders')
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_trades')),
    )

    # --- signals ---
    op.create_table(
        'signals',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('market_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('sentiment', sa.String(), nullable=False),
        sa.Column('confidence', sa.Numeric(), nullable=False),
        sa.Column('raw_data', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('cycle_id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['market_id'], ['markets.id'],
            name=op.f('fk_signals_market_id_markets')
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_signals')),
    )
    op.create_index(
        'ix_signals_market_source_created',
        'signals',
        ['market_id', 'source', 'created_at'],
        unique=False,
    )

    # --- model_outputs ---
    op.create_table(
        'model_outputs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('market_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('p_model', sa.Numeric(), nullable=False),
        sa.Column('p_market', sa.Numeric(), nullable=True),
        sa.Column('confidence_low', sa.Numeric(), nullable=False),
        sa.Column('confidence_high', sa.Numeric(), nullable=False),
        sa.Column('signal_weights', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('model_version', sa.String(), nullable=False),
        sa.Column('used_llm', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('cycle_id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['market_id'], ['markets.id'],
            name=op.f('fk_model_outputs_market_id_markets')
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_model_outputs')),
    )
    op.create_index(
        'ix_model_outputs_market_created',
        'model_outputs',
        ['market_id', 'created_at'],
        unique=False,
    )

    # --- performance_metrics ---
    op.create_table(
        'performance_metrics',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('metric_name', sa.String(), nullable=False),
        sa.Column('metric_value', sa.Numeric(), nullable=False),
        sa.Column('period', sa.String(), nullable=True),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_performance_metrics')),
    )
    op.create_index(
        'ix_perf_metrics_name_computed',
        'performance_metrics',
        ['metric_name', 'computed_at'],
        unique=False,
    )


def downgrade() -> None:
    """Drop all PMTB tables."""
    op.drop_index('ix_perf_metrics_name_computed', table_name='performance_metrics')
    op.drop_table('performance_metrics')

    op.drop_index('ix_model_outputs_market_created', table_name='model_outputs')
    op.drop_table('model_outputs')

    op.drop_index('ix_signals_market_source_created', table_name='signals')
    op.drop_table('signals')

    op.drop_table('trades')
    op.drop_table('positions')

    op.drop_index(op.f('ix_orders_kalshi_order_id'), table_name='orders')
    op.drop_index(op.f('ix_orders_status'), table_name='orders')
    op.drop_index(op.f('ix_orders_market_id'), table_name='orders')
    op.drop_table('orders')

    op.drop_index(op.f('ix_markets_close_time'), table_name='markets')
    op.drop_index(op.f('ix_markets_status'), table_name='markets')
    op.drop_index(op.f('ix_markets_ticker'), table_name='markets')
    op.drop_table('markets')
