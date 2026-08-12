"""add product identity fields

Revision ID: b42d62e76c2f
Revises: 71666be7efdd
Create Date: 2026-08-11

Phase 2A: give products a clean identity (brand, quantity, unit) and record
the CURRENT selling price separately from the suggested price.

All new columns are NULLABLE on purpose:
- existing products have no brand/quantity/unit/selling_price, and we must
  NOT fabricate values for them (NULL = "genuinely unknown").
- selling_price (current price) is distinct from suggested_price (cost x margin).

No data backfill needed - no existing row is modified.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b42d62e76c2f'
down_revision = '71666be7efdd'
branch_labels = None
depends_on = None


def upgrade():
    # ---- Product identity + current price ----
    op.add_column('product', sa.Column('brand', sa.String(length=80), nullable=True))
    op.add_column('product', sa.Column('quantity', sa.Numeric(precision=10, scale=3),
                                       nullable=True))
    op.add_column('product', sa.Column('unit', sa.String(length=20), nullable=True))
    op.add_column('product', sa.Column('selling_price', sa.Numeric(precision=10, scale=2),
                                       nullable=True))

    # ---- Audit trail also snapshots the current selling price ----
    op.add_column('price_history', sa.Column('selling_price',
                                             sa.Numeric(precision=10, scale=2),
                                             nullable=True))


def downgrade():
    op.drop_column('price_history', 'selling_price')
    op.drop_column('product', 'selling_price')
    op.drop_column('product', 'unit')
    op.drop_column('product', 'quantity')
    op.drop_column('product', 'brand')
