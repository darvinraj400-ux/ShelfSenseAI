"""Remove preferred_language column (language preferences removed)

Revision ID: c8b7e2f1d0a3
Revises: e1f2a3b4c5d6
Create Date: 2026-08-26

Reverts the 4-language localization feature. Drops the preferred_language
column from the user table that was added in the previous migration.

Hand-written to avoid schema drift from autogenerate.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c8b7e2f1d0a3'
down_revision = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column('user', 'preferred_language')


def downgrade():
    op.add_column('user',
                  sa.Column('preferred_language', sa.String(5),
                            nullable=False, server_default='en'))
    op.alter_column('user', 'preferred_language',
                    existing_type=sa.String(5), server_default=None)
