"""add shop model - shop-based product ownership

Revision ID: 71666be7efdd
Revises: d5ae4caca1e9
Create Date: 2026-08-11 21:08:56.517434

Ownership changes:
    BEFORE:  User → Product            (product.user_id)
    AFTER:   User → Shop → Product     (user.shop_id, product.shop_id)

Data migration strategy (NO data loss):
  1. Create the `shop` table.
  2. Add nullable `shop_id` to `user` and `product`.
  3. Backfill:
       - Create ONE "Demo Retail Shop" and assign owner@demo.my,
         manager@demo.my, staff@demo.my to it.
       - Every OTHER existing user gets their own private shop
         ("Shop of <email>") so their products stay isolated.
       - Every product is assigned to the shop of its current owner.
  4. Enforce NOT NULL and add the new foreign keys.
  5. Drop the old product.user_id FK + column (last, after data is safe).
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '71666be7efdd'
down_revision = 'd5ae4caca1e9'
branch_labels = None
depends_on = None

# ---------------------------------------------------------------
# The demo accounts that must share ONE shop.
# ---------------------------------------------------------------
DEMO_SHOP_NAME = 'Demo Retail Shop'
DEMO_USERS = {'owner@demo.my', 'manager@demo.my', 'staff@demo.my'}


def _backfill():
    """Assign every user and product to a shop (no data loss)."""
    bind = op.get_bind()
    meta = sa.MetaData()
    shop_t = sa.Table('shop', meta, autoload_with=bind)
    user_t = sa.Table('user', meta, autoload_with=bind)
    product_t = sa.Table('product', meta, autoload_with=bind)

    # ---- 1. the demo shop (create once) ----
    demo_shop_id = None
    existing = bind.execute(
        sa.select(shop_t.c.id).where(shop_t.c.name == DEMO_SHOP_NAME)
    ).first()
    if existing is not None:
        demo_shop_id = existing[0]
    else:
        res = bind.execute(shop_t.insert().values(
            name=DEMO_SHOP_NAME, created_at=datetime.now(timezone.utc)))
        demo_shop_id = res.inserted_primary_key[0]

    # ---- 2. every user without a shop ----
    # demo accounts → demo shop; everyone else → their own private shop
    users = bind.execute(
        sa.select(user_t.c.id, user_t.c.email, user_t.c.shop_id)
    ).fetchall()
    for uid, email, sid in users:
        if sid is not None:
            continue
        if email in DEMO_USERS:
            new_sid = demo_shop_id
        else:
            res = bind.execute(shop_t.insert().values(
                name=f'Shop of {email}', created_at=datetime.now(timezone.utc)))
            new_sid = res.inserted_primary_key[0]
        bind.execute(user_t.update().where(user_t.c.id == uid).values(shop_id=new_sid))

    # ---- 3. every product goes to its owner's shop ----
    products = bind.execute(
        sa.select(product_t.c.id, product_t.c.user_id)
    ).fetchall()
    for pid, owner_id in products:
        owner_shop = bind.execute(
            sa.select(user_t.c.shop_id).where(user_t.c.id == owner_id)
        ).first()
        if owner_shop is not None:
            bind.execute(
                product_t.update().where(product_t.c.id == pid)
                .values(shop_id=owner_shop[0]))


def upgrade():
    # 1. the shop table
    op.create_table(
        'shop',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )

    # 2. new columns (nullable first so existing rows can be backfilled)
    op.add_column('user', sa.Column('shop_id', sa.Integer(), nullable=True))
    op.add_column('product', sa.Column('shop_id', sa.Integer(), nullable=True))

    # 3. data migration: users → shops, products → their owner's shop
    _backfill()

    # 4. now safe to enforce NOT NULL + wire the new FKs
    op.alter_column('user', 'shop_id', existing_type=sa.Integer(),
                    nullable=False)
    op.alter_column('product', 'shop_id', existing_type=sa.Integer(),
                    nullable=False)
    op.create_foreign_key('fk_user_shop_id', 'user', 'shop',
                          ['shop_id'], ['id'])
    op.create_foreign_key('fk_product_shop_id', 'product', 'shop',
                          ['shop_id'], ['id'])

    # 5. remove the old user-level ownership (only after products are safe)
    op.drop_constraint('product_ibfk_1', 'product', type_='foreignkey')
    op.drop_column('product', 'user_id')


def downgrade():
    # restore product.user_id, pointing at the owner of the product's shop
    op.add_column('product', sa.Column('user_id', sa.Integer(), nullable=True))

    bind = op.get_bind()
    meta = sa.MetaData()
    user_t = sa.Table('user', meta, autoload_with=bind)
    product_t = sa.Table('product', meta, autoload_with=bind)

    products = bind.execute(
        sa.select(product_t.c.id, product_t.c.shop_id)
    ).fetchall()
    for pid, sid in products:
        # prefer the shop's owner; fall back to any member of the shop
        target = bind.execute(
            sa.select(user_t.c.id).where(
                sa.and_(user_t.c.shop_id == sid, user_t.c.role == 'owner')
            ).limit(1)).first()
        if target is None:
            target = bind.execute(
                sa.select(user_t.c.id).where(
                    user_t.c.shop_id == sid).limit(1)).first()
        if target is not None:
            bind.execute(
                product_t.update().where(product_t.c.id == pid)
                .values(user_id=target[0]))

    # only enforce NOT NULL if every product got a user (should always hold)
    null_count = bind.execute(
        sa.select(sa.func.count()).select_from(product_t)
        .where(product_t.c.user_id.is_(None))).scalar()
    op.alter_column('product', 'user_id', existing_type=sa.Integer(),
                    nullable=(null_count > 0))
    op.create_foreign_key('product_ibfk_1', 'product', 'user',
                          ['user_id'], ['id'])

    # remove shop ownership and the shop table
    op.drop_constraint('fk_product_shop_id', 'product', type_='foreignkey')
    op.drop_column('product', 'shop_id')
    op.drop_constraint('fk_user_shop_id', 'user', type_='foreignkey')
    op.drop_column('user', 'shop_id')
    op.drop_table('shop')
