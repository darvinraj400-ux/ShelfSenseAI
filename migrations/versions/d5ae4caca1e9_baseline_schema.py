"""baseline schema

Revision ID: d5ae4caca1e9
Revises:
Create Date: 2026-08-11 (restored - original migration file was missing
while the live database was already stamped at this revision)

This is the PRE-SHOP baseline: the original schema where products belong
directly to a user (product.user_id). The next migration (add shop model)
replaces that with shop-based ownership.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5ae4caca1e9'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # ---- auth / accounts ----
    op.create_table(
        'user',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('email', sa.String(length=120), nullable=False),
        sa.Column('password_hash', sa.String(length=256), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False,
                  server_default='staff'),
        sa.UniqueConstraint('email', name='email'),
    )

    # ---- core business object ----
    op.create_table(
        'product',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('cost_price', sa.Float(), nullable=False),
        sa.Column('target_margin', sa.Float(), nullable=False),
        sa.Column('baseline_margin', sa.Float(), nullable=True),
        sa.Column('category', sa.String(length=80), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name='product_ibfk_1'),
    )

    # ---- audit trail ----
    op.create_table(
        'price_history',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('cost_price', sa.Float(), nullable=False),
        sa.Column('target_margin', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['product_id'], ['product.id'],
                                name='price_history_ibfk_1'),
    )

    # ---- PriceCatcher reference data (unchanged by later phases) ----
    op.create_table(
        'lookup_item',
        sa.Column('item_code', sa.String(length=50), primary_key=True),
        sa.Column('item', sa.String(length=255), nullable=False),
        sa.Column('unit', sa.String(length=50), nullable=True),
        sa.Column('item_group', sa.String(length=100), nullable=True),
        sa.Column('item_category', sa.String(length=100), nullable=True),
    )
    op.create_table(
        'lookup_premise',
        sa.Column('premise_code', sa.String(length=50), primary_key=True),
        sa.Column('premise', sa.String(length=255), nullable=False),
        sa.Column('address', sa.String(length=255), nullable=True),
        sa.Column('premise_type', sa.String(length=50), nullable=True),
        sa.Column('state', sa.String(length=50), nullable=True),
        sa.Column('district', sa.String(length=50), nullable=True),
    )
    op.create_table(
        'price',
        sa.Column('price_id', sa.Integer(), primary_key=True,
                  autoincrement=True),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('price', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('item_code', sa.String(length=50), nullable=False),
        sa.Column('premise_code', sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(['item_code'], ['lookup_item.item_code'],
                                name='fk_price_item'),
        sa.ForeignKeyConstraint(['premise_code'], ['lookup_premise.premise_code'],
                                name='fk_price_premise'),
    )
    op.create_table(
        'price_catcher_item',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('item_code', sa.String(length=60), nullable=False),
        sa.Column('item', sa.String(length=255), nullable=False),
        sa.Column('unit', sa.String(length=50), nullable=True),
        sa.Column('item_group', sa.String(length=100), nullable=True),
        sa.Column('item_category', sa.String(length=100), nullable=True),
        sa.UniqueConstraint('item_code', name='uq_pc_item_code'),
    )


def downgrade():
    op.drop_table('price_catcher_item')
    op.drop_table('price')
    op.drop_table('lookup_premise')
    op.drop_table('lookup_item')
    op.drop_table('price_history')
    op.drop_table('product')
    op.drop_table('user')
