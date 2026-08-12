"""add shop invitations

Revision ID: 9cdb356ab630
Revises: 7c699f870e84
Create Date: 2026-08-12

Phase 2C: employee invitations & shop membership.

- shop_invitation: one row per invite (shop, invited-by owner, email, role,
  secure token, status, created/expires). No data backfill needed - the
  existing team already exists via user.shop_id.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9cdb356ab630'
down_revision = '7c699f870e84'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'shop_invitation',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('shop_id', sa.Integer(), nullable=False),
        sa.Column('invited_by_user_id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=120), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('token', sa.String(length=128), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['invited_by_user_id'], ['user.id'], ),
        sa.ForeignKeyConstraint(['shop_id'], ['shop.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    # token must be unique + indexed: it is the (secret) accept credential.
    op.create_index(op.f('ix_shop_invitation_token'), 'shop_invitation',
                    ['token'], unique=True)
    op.create_index(op.f('ix_shop_invitation_shop_id'), 'shop_invitation',
                    ['shop_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_shop_invitation_shop_id'), table_name='shop_invitation')
    op.drop_index(op.f('ix_shop_invitation_token'), table_name='shop_invitation')
    op.drop_table('shop_invitation')
