"""add market_refresh_run

Revision ID: 33b959a98288
Revises: f9e8d7c6b5a4
Create Date: 2026-09-11

Phase 10B: persistent market refresh audit.

One row per source refresh attempt (PriceCatcher / ManaMurah).
Makes the Phase 10A orchestrator observable without inspecting
terminal logs. Per-source rows preserve partial-failure truth
(source=all that half-fails still shows PriceCatcher success +
ManaMurah failed).

No existing table is touched. No 1.97M row backfill.
The new table is indexed on (source_name, started_at) for
recent-run lookups (status page, latest_run helpers).

Hand-written (same reason as prior migrations): autogenerate
would pick up unrelated drift.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '33b959a98288'
down_revision = 'f9e8d7c6b5a4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'market_refresh_run',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source_name', sa.String(length=50), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('inserted', sa.Integer(), nullable=False),
        sa.Column('updated', sa.Integer(), nullable=False),
        sa.Column('duplicates_skipped', sa.Integer(), nullable=False),
        sa.Column('rejected', sa.Integer(), nullable=False),
        sa.Column('errors', sa.Integer(), nullable=False),
        sa.Column('latest_observed_at', sa.DateTime(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('triggered_by', sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_market_refresh_run_source_started', 'market_refresh_run',
                    ['source_name', 'started_at'], unique=False)


def downgrade():
    op.drop_index('ix_market_refresh_run_source_started', table_name='market_refresh_run')
    op.drop_table('market_refresh_run')
