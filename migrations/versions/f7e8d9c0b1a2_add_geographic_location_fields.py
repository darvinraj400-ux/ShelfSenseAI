"""Add geographic location fields for localized market intelligence

Revision ID: f7e8d9c0b1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-08-26

Phase 4 add-on: Geographic Market Intelligence.
Adds state and district columns to two tables:

  shop:
    - state (String(50), nullable): Malaysian state (e.g. 'Johor').
    - district (String(50), nullable): District within the state.

  market_price_observation:
    - state (String(50), nullable, indexed): State where the price
      was observed. Enables the market_analysis engine to filter
      observations by the shop's geographic region.
    - district (nullable): District for finer-grained filtering.

Existing rows are unaffected: both columns are nullable so shops
and observations from earlier phases keep their current NULL values.

Hand-written on purpose: autogenerate picks up pre-existing drift
between earlier hand-written migrations and the live MySQL schema;
this is the minimal four-column delta.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f7e8d9c0b1a2'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    # --- shop table: geographic location for the shop ---
    # state and district are nullable so existing shops (from Phase 1-2C)
    # are unaffected. New shops created via the updated registration form
    # will populate these fields.
    op.add_column('shop',
                  sa.Column('state', sa.String(50), nullable=True))
    op.add_column('shop',
                  sa.Column('district', sa.String(50), nullable=True))

    # --- market_price_observation: geographic data for each observation ---
    # state is indexed to enable efficient geographic filtering in the
    # market_analysis engine (WHERE state = ?). district is not indexed
    # because the fallback chain narrows from state -> district -> national,
    # so district queries always run within a state-scoped result set.
    op.add_column('market_price_observation',
                  sa.Column('state', sa.String(50), nullable=True))
    op.add_column('market_price_observation',
                  sa.Column('district', sa.String(50), nullable=True))
    op.create_index(op.f('ix_market_price_observation_state'),
                    'market_price_observation', ['state'])


def downgrade():
    op.drop_index(op.f('ix_market_price_observation_state'),
                  table_name='market_price_observation')
    op.drop_column('market_price_observation', 'district')
    op.drop_column('market_price_observation', 'state')
    op.drop_column('shop', 'district')
    op.drop_column('shop', 'state')
