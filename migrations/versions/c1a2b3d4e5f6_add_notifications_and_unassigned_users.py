"""add notifications and unassigned employee accounts

Revision ID: c1a2b3d4e5f6
Revises: 9cdb356ab630
Create Date: 2026-08-17

Employee registration & in-app notifications (Phase 2D):

- user.shop_id becomes NULLABLE: an employee account created via the
  "Join an existing shop" path has NO shop membership until an owner's
  invitation is explicitly accepted. Existing rows are untouched (no
  backfill needed - every current user keeps their shop_id).
- notification: one row per in-app notification (e.g. "Shop Invitation").
  Notifications belong to a user and may reference the invitation that
  caused them. No data backfill needed.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1a2b3d4e5f6'
down_revision = '9cdb356ab630'
branch_labels = None
depends_on = None


def upgrade():
    # Unassigned employee accounts have no shop membership yet.
    op.alter_column('user', 'shop_id', existing_type=sa.Integer(),
                    nullable=True)

    op.create_table(
        'notification',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(length=50), nullable=False),
        sa.Column('title', sa.String(length=120), nullable=False),
        sa.Column('message', sa.String(length=255), nullable=False),
        sa.Column('invitation_id', sa.Integer(), nullable=True),
        sa.Column('is_read', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['invitation_id'], ['shop_invitation.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_notification_user_id'), 'notification',
                    ['user_id'], unique=False)
    op.create_index(op.f('ix_notification_invitation_id'), 'notification',
                    ['invitation_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_notification_invitation_id'), table_name='notification')
    op.drop_index(op.f('ix_notification_user_id'), table_name='notification')
    op.drop_table('notification')
    # NOTE: only safe if no unassigned (shop_id NULL) users exist.
    op.alter_column('user', 'shop_id', existing_type=sa.Integer(),
                    nullable=False)
