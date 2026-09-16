"""Add premise-level market observations

Revision ID: e5d4c3b2a1f0
Revises: c8b7e2f1d0a3
Create Date: 2026-09-08

Phase 6: premise-level PriceCatcher observations.

The old PriceCatcher ETL aggregated every raw price down to one row per
(item, date, state, district) with AVG(price), destroying store-level
information (retention ~17%). The ETL now stores ONE MarketPriceObservation
per raw price record, so each observation must answer "which store reported
this price?".

Changes:
  * market_price_observation.premise_code (VARCHAR(20), NULL):
      Stable premise identifier (lookup_premise.premise_code). Premise
      metadata (name/address/type/state/district) is NOT duplicated here —
      it continues to resolve through the `lookup_premise` archive table.
      NULL for non-PriceCatcher sources (ManaMurah/FAMA/manual) that have
      no premise concept.
      No foreign key on purpose: an observation is a historical snapshot
      and must survive premise renames/removals in the import-managed
      lookup table. The raw `price` table already enforces FK integrity.
  * UNIQUE uq_market_obs_premise (market_item_id, premise_code, observed_at):
      Natural key of a PriceCatcher observation; the DB-level guarantee
      that a repeated ETL run cannot create duplicate observations.
      (NULL premise_code rows — non-PriceCatcher sources — are exempt from
      uniqueness in MySQL/MariaDB; their idempotency is enforced by the
      ingestion layer on (market_item_id, observed_at, state, district).)
  * INDEX ix_market_obs_item_geo (market_item_id, state, district,
      observed_at): the hot query in services/market_analysis.py filters
      by market_item_id + state (+ district) and orders by observed_at.
  * INDEX ix_market_item_external (external_id) on market_item: the ETL
      joins raw price rows to MarketItem by (source_id, external_id);
      without it every month rebuild scanned all 405 items per row.
  * UNIQUE uq_price_natural (date, premise_code, item_code) on the raw
      `price` table: the raw archive's natural key, which import_pricecatcher
      also creates via CREATE ... IF NOT EXISTS. Added here (with IF NOT
      EXISTS) so fresh `flask db upgrade` installs get it from migrations
      regardless of which tool runs first.

Hand-written on purpose (same reason as prior migrations): autogenerate
picks up pre-existing drift between earlier hand-written migrations and
the live MySQL schema.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = 'e5d4c3b2a1f0'
down_revision = 'c8b7e2f1d0a3'
branch_labels = None
depends_on = None


def _index_exists(bind, table_name, index_name):
    """True when `index_name` already exists on `table_name`.

    MySQL/TiDB use `information_schema.statistics` (there is no
    `CREATE INDEX IF NOT EXISTS` / `DROP INDEX IF EXISTS` support), so
    index DDL is guarded by this check to stay portable across MariaDB
    (local dev) and TiDB (production).
    """
    return bool(bind.execute(text(
        "SELECT COUNT(*) FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() "
        "AND table_name = :tbl AND index_name = :idx"
    ), {"tbl": table_name, "idx": index_name}).scalar())


def upgrade():
    # --- premise identity on each observation -------------------------
    op.add_column('market_price_observation',
                  sa.Column('premise_code', sa.String(20), nullable=True))
    op.create_index('uq_market_obs_premise', 'market_price_observation',
                    ['market_item_id', 'premise_code', 'observed_at'],
                    unique=True)
    # --- geographic query index (item + state + district + date) ------
    op.create_index('ix_market_obs_item_geo', 'market_price_observation',
                    ['market_item_id', 'state', 'district', 'observed_at'])
    # --- ETL join index (raw price -> MarketItem by external code) ----
    op.create_index('ix_market_item_external', 'market_item',
                    ['external_id'])
    # --- raw archive natural key (order-independent with the importer) -
    # Guarded: MySQL/TiDB have no CREATE INDEX IF NOT EXISTS. The baseline
    # migration does not add this index to `price`, so create it when absent.
    bind = op.get_bind()
    if not _index_exists(bind, 'price', 'uq_price_natural'):
        op.create_index('uq_price_natural', 'price',
                        ['date', 'premise_code', 'item_code'], unique=True)


def downgrade():
    bind = op.get_bind()
    if _index_exists(bind, 'price', 'uq_price_natural'):
        op.drop_index('uq_price_natural', table_name='price')
    op.drop_index('ix_market_item_external', table_name='market_item')
    op.drop_index('ix_market_obs_item_geo',
                  table_name='market_price_observation')
    op.drop_index('uq_market_obs_premise',
                  table_name='market_price_observation')
    op.drop_column('market_price_observation', 'premise_code')