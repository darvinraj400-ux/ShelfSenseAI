"""add sales inventory and adjustments

Revision ID: 7c699f870e84
Revises: b42d62e76c2f
Create Date: 2026-08-12 21:07:56.501037

Phase 2B: sales recording + inventory tracking.

- sale:               one row per completed sale (per-unit price snapshot)
- inventory:          one row per product (current_stock / minimum_stock)
- inventory_adjustment: manual stock changes with a reason (traceability)

Existing products get an inventory row with ZERO stock (minimum_stock 0).
No historical sales are fabricated - the sale table starts empty.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7c699f870e84'
down_revision = 'b42d62e76c2f'
branch_labels = None
depends_on = None


def upgrade():
    # ---- sale: one row per completed sale ----
    op.create_table(
        'sale',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('shop_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column('selling_price', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('sold_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['product_id'], ['product.id'], ),
        sa.ForeignKeyConstraint(['shop_id'], ['shop.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_sale_product_id'), 'sale', ['product_id'], unique=False)
    op.create_index(op.f('ix_sale_shop_id'), 'sale', ['shop_id'], unique=False)

    # ---- inventory: one row per product ----
    op.create_table(
        'inventory',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('shop_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('current_stock', sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column('minimum_stock', sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['product_id'], ['product.id'], ),
        sa.ForeignKeyConstraint(['shop_id'], ['shop.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('product_id'),
    )
    op.create_index(op.f('ix_inventory_shop_id'), 'inventory', ['shop_id'], unique=False)

    # ---- inventory_adjustment: manual stock changes with a reason ----
    op.create_table(
        'inventory_adjustment',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('shop_id', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('quantity_change', sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column('reason', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['product_id'], ['product.id'], ),
        sa.ForeignKeyConstraint(['shop_id'], ['shop.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_inventory_adjustment_product_id'),
                    'inventory_adjustment', ['product_id'], unique=False)
    op.create_index(op.f('ix_inventory_adjustment_shop_id'),
                    'inventory_adjustment', ['shop_id'], unique=False)

    # ---- existing products: zero stock (never invent stock levels) ----
    op.execute("""
        INSERT INTO inventory (shop_id, product_id, current_stock, minimum_stock)
        SELECT shop_id, id, 0, 0 FROM product
    """)


def downgrade():
    op.drop_index(op.f('ix_inventory_adjustment_shop_id'), table_name='inventory_adjustment')
    op.drop_index(op.f('ix_inventory_adjustment_product_id'), table_name='inventory_adjustment')
    op.drop_table('inventory_adjustment')
    op.drop_index(op.f('ix_inventory_shop_id'), table_name='inventory')
    op.drop_table('inventory')
    op.drop_index(op.f('ix_sale_shop_id'), table_name='sale')
    op.drop_index(op.f('ix_sale_product_id'), table_name='sale')
    op.drop_table('sale')
