"""Add Phase 3A Market Data Foundation

Revision ID: 3aed64f374e8
Revises: d6e7f8a9b0c1
Create Date: 2026-08-18

Phase 3A: external market data (PriceCatcher, online retailers, manual
entry). Four NEW tables only - no existing table is touched, so shop
Product/Inventory/Sale behavior is unchanged. Product stays source-
independent: the ONLY bridge to market data is the mapping table
product_market_match (shop_product_id <-> market_item_id), never a
hard FK on product.

NOTE: this was hand-written on purpose. `flask db migrate` autogenerate
picks up pre-existing drift between earlier hand-written migrations and
the live MySQL schema (lookup_item/lookup_premise/price type changes,
index differences on inventory/sale/notification/...); running that
generated script would have altered unrelated tables. This script is
the minimal, safe delta: create the four new tables and nothing else.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3aed64f374e8'
down_revision = 'd6e7f8a9b0c1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'market_source',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('source_type',
                  sa.Enum('government', 'online_retailer', 'manual'),
                  nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'market_item',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.String(length=100), nullable=True),
        sa.Column('raw_title', sa.String(length=255), nullable=False),
        sa.Column('normalized_title', sa.String(length=255), nullable=True),
        sa.Column('brand', sa.String(length=100), nullable=True),
        sa.Column('category', sa.String(length=100), nullable=True),
        sa.Column('package_quantity', sa.Numeric(precision=10, scale=3),
                  nullable=False),
        sa.Column('package_unit', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['source_id'], ['market_source.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_market_item_normalized_title'), 'market_item',
                    ['normalized_title'], unique=False)
    op.create_table(
        'market_price_observation',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('market_item_id', sa.Integer(), nullable=False),
        sa.Column('regular_price', sa.Numeric(precision=10, scale=2),
                  nullable=False),
        sa.Column('promo_price', sa.Numeric(precision=10, scale=2),
                  nullable=True),
        sa.Column('is_on_promo', sa.Boolean(), nullable=False),
        sa.Column('effective_price', sa.Numeric(precision=10, scale=2),
                  nullable=False),
        sa.Column('normalized_unit_price', sa.Numeric(precision=10, scale=4),
                  nullable=False),
        sa.Column('observed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['market_item_id'], ['market_item.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_market_price_observation_observed_at'),
                    'market_price_observation', ['observed_at'], unique=False)
    op.create_table(
        'product_market_match',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('shop_product_id', sa.Integer(), nullable=False),
        sa.Column('market_item_id', sa.Integer(), nullable=False),
        sa.Column('confidence_score', sa.Numeric(precision=3, scale=2),
                  nullable=True),
        sa.Column('match_type', sa.Enum('exact', 'fuzzy', 'manual'),
                  nullable=False),
        sa.Column('is_verified', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['market_item_id'], ['market_item.id']),
        sa.ForeignKeyConstraint(['shop_product_id'], ['product.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('shop_product_id', 'market_item_id',
                            name='uq_product_market_match'),
    )


def downgrade():
    op.drop_table('product_market_match')
    op.drop_index(op.f('ix_market_price_observation_observed_at'),
                  table_name='market_price_observation')
    op.drop_table('market_price_observation')
    op.drop_index(op.f('ix_market_item_normalized_title'),
                  table_name='market_item')
    op.drop_table('market_item')
    op.drop_table('market_source')
