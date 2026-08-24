"""add KPDN price control regulation fields to product

Revision ID: a1b2c3d4e5f6
Revises: b4f5a6c7d8e9
Create Date: 2026-08-24

Phase 4: Barangan Kawalan (Price-Controlled Goods) regulation.
Adds two columns to the product table:
  - is_price_controlled (Boolean, default False)
  - government_ceiling_price (Float, nullable)

Existing products are unaffected: is_price_controlled defaults to False
and government_ceiling_price defaults to NULL.

Hand-written on purpose (same reason as prior migrations): autogenerate
picks up pre-existing drift between earlier hand-written migrations
and the live MySQL schema; this is the minimal two-column delta.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'b4f5a6c7d8e9'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('product',
                  sa.Column('is_price_controlled', sa.Boolean(),
                            nullable=False, server_default=sa.false()))
    op.add_column('product',
                  sa.Column('government_ceiling_price', sa.Float(),
                            nullable=True))
    # Drop the server_default so the schema matches the model
    # (client-side default only), avoiding drift.
    op.alter_column('product', 'is_price_controlled',
                    existing_type=sa.Boolean(), server_default=None)


def downgrade():
    op.drop_column('product', 'government_ceiling_price')
    op.drop_column('product', 'is_price_controlled')
