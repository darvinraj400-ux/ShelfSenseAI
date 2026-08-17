"""add user to inventory adjustment

Revision ID: d6e7f8a9b0c1
Revises: c1a2b3d4e5f6
Create Date: 2026-08-17

Inventory UX fix: record WHO performed each manual stock movement so the
inventory audit trail is complete. The column is nullable because existing
adjustment rows predate user tracking - no backfill needed (historical rows
simply show no user). No data is touched.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd6e7f8a9b0c1'
down_revision = 'c1a2b3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('inventory_adjustment',
                  sa.Column('user_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_inventory_adjustment_user_id',
                          'inventory_adjustment', 'user',
                          ['user_id'], ['id'])
    op.create_index(op.f('ix_inventory_adjustment_user_id'),
                    'inventory_adjustment', ['user_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_inventory_adjustment_user_id'),
                  table_name='inventory_adjustment')
    op.drop_constraint('fk_inventory_adjustment_user_id',
                       'inventory_adjustment', type_='foreignkey')
    op.drop_column('inventory_adjustment', 'user_id')
