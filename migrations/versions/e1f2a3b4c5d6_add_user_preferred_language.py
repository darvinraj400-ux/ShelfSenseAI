"""Add user preferred_language for 4-language localization

Revision ID: e1f2a3b4c5d6
Revises: f7e8d9c0b1a2
Create Date: 2026-08-26

Phase 5 add-on: User Profile & 4-Language Localization.
Adds a preferred_language column to the user table to store
the user's UI language choice:

  - 'en' (English) — default
  - 'ms' (Bahasa Melayu)
  - 'zh' (Chinese/Mandarin)
  - 'ta' (Tamil)

Existing users are unaffected: the column defaults to 'en'.
The translations.py module provides the translation dictionary
and a _t(key, lang) helper function that is injected into all
templates via a Flask context processor.

Hand-written on purpose: autogenerate picks up pre-existing drift
between earlier hand-written migrations and the live MySQL schema;
this is the minimal one-column delta.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e1f2a3b4c5d6'
down_revision = 'f7e8d9c0b1a2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user',
                  sa.Column('preferred_language', sa.String(5),
                            nullable=False, server_default='en'))
    # Drop the server_default so the schema matches the model
    # (client-side default only), avoiding drift.
    op.alter_column('user', 'preferred_language',
                    existing_type=sa.String(5), server_default=None)


def downgrade():
    op.drop_column('user', 'preferred_language')
