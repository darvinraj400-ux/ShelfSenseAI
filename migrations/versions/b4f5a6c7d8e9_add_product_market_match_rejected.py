"""add is_rejected to product_market_match

Revision ID: b4f5a6c7d8e9
Revises: 3aed64f374e8
Create Date: 2026-08-18

Phase 3C: persist REJECTED match suggestions so they never reappear
after a re-match. Existing rows are all unverified/unrejected (or
already verified) - they get is_rejected = 0 via the server default.

Hand-written on purpose (same reason as 3aed64f374e8): autogenerate
picks up pre-existing drift between earlier hand-written migrations
and the live MySQL schema; this is the minimal single-column delta.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b4f5a6c7d8e9'
down_revision = '3aed64f374e8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('product_market_match',
                  sa.Column('is_rejected', sa.Boolean(), nullable=False,
                            server_default=sa.false()))
    # Backfill happened via the server default; drop it so the schema
    # matches the model (client-side default only), avoiding drift.
    op.alter_column('product_market_match', 'is_rejected',
                    existing_type=sa.Boolean(), server_default=None)


def downgrade():
    op.drop_column('product_market_match', 'is_rejected')
