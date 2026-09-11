"""add pricing recommendation decisions

Revision ID: f9e8d7c6b5a4
Revises: e5d4c3b2a1f0
Create Date: 2026-09-09

Phase 8: pricing decision workflow & auditability.

- pricing_recommendation_decision: one row per recommendation snapshot the
  system showed a retailer, plus that retailer's explicit decision
  (PENDING -> APPLIED | DISMISSED). Snapshot columns freeze what the
  retailer saw at generation time (recommended price, current price,
  engine status, market tier/median, evidence rating, trend) so a decision
  stays explainable after the market moves on. This table is deliberately
  separate from price_history: price_history answers "what happened to the
  actual selling price?", this table answers "what did the system recommend
  and what did the retailer decide?".

No data backfill needed - decisions start empty; PENDING rows are recorded
lazily the first time an owner/manager reviews a recommendation.

Hand-written on purpose (same reason as prior migrations): autogenerate
picks up pre-existing drift between earlier hand-written migrations and
the live MySQL schema; this is the minimal one-table delta.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f9e8d7c6b5a4'
down_revision = 'e5d4c3b2a1f0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'pricing_recommendation_decision',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('shop_id', sa.Integer(), nullable=False),
        sa.Column('recommended_price', sa.Float(), nullable=False),
        sa.Column('current_price', sa.Float(), nullable=True),
        sa.Column('recommendation_status', sa.String(length=20), nullable=False),
        sa.Column('market_evidence', sa.String(length=20), nullable=True),
        sa.Column('market_tier', sa.String(length=20), nullable=True),
        sa.Column('market_median', sa.Float(), nullable=True),
        sa.Column('trend_classification', sa.String(length=20), nullable=True),
        sa.Column('trend_percent', sa.Float(), nullable=True),
        sa.Column('guardrails_applied', sa.String(length=255), nullable=True),
        sa.Column('generated_at', sa.DateTime(), nullable=False),
        sa.Column('decision', sa.String(length=20), nullable=False),
        sa.Column('decision_reason', sa.String(length=255), nullable=True),
        sa.Column('decided_by', sa.Integer(), nullable=True),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['product_id'], ['product.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['shop_id'], ['shop.id'], ondelete='CASCADE'),
        # A decision's audit value is tied to its product/shop: when either
        # is deleted the decision goes with it (DB-level, works for both
        # ORM deletes and raw-SQL purges). The deciding USER may be removed
        # from the shop later — the decision survives with decided_by=NULL.
        sa.ForeignKeyConstraint(['decided_by'], ['user.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    # The hot query is "recent decisions for one product" (history card)
    # and "is there an identical PENDING snapshot already?" (dedupe) -
    # both filter by product_id, so one plain index suffices.
    op.create_index(op.f('ix_pricing_recommendation_decision_product_id'),
                    'pricing_recommendation_decision', ['product_id'],
                    unique=False)
    # Cross-shop authorization compares shop_id on the row itself.
    op.create_index(op.f('ix_pricing_recommendation_decision_shop_id'),
                    'pricing_recommendation_decision', ['shop_id'],
                    unique=False)
    # decided_by is a rare FK (only set on apply/dismiss); no index needed.


def downgrade():
    # Drop the TABLE first: MySQL needs the shop_id/product_id indexes to
    # back the FK constraints, so removing the indexes before the table
    # fails with 'needed in a foreign key constraint' (1553).
    op.drop_table('pricing_recommendation_decision')
