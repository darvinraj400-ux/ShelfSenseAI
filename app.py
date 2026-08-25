"""
============================================================
 ShelfSense AI - Backend (app.py)
============================================================
 This single file contains the ENTIRE backend of ShelfSense AI:

   1. App configuration          (Flask, DB connection, secrets)
   2. Database models            (Shop, User, Product, PriceHistory, PriceCatcher tables)
   3. Authentication             (register / login / logout, Flask-Login sessions)
   4. The pricing & PCAPA logic  (suggested price, baseline margin, compliance check)
   5. The audit trail            (PriceHistory rows on every cost/margin change)
   6. APIs for the frontend      (GET /autocomplete - feeds the name autofill)
   7. Error handling             (404 / 403 / 500 / CSRF)

 The frontend (templates/) renders the pages; this file is the engine that
 makes decisions and talks to the MySQL database.
============================================================
"""

# ------------------------- IMPORTS -------------------------
# Each import group serves one purpose:
from flask import (Flask, render_template, redirect, url_for, flash,
                   request, jsonify, abort)
#   - Flask:            the web framework itself
#   - render_template:  fills an HTML template with data
#   - redirect/url_for: send the browser to another route
#   - flash:            show a one-time message on the next page (e.g. "Product added!")
#   - request:          read what the browser sent (form data, query strings)
#   - jsonify:          return JSON data to the frontend (used by autocomplete)
#   - abort:            stop with an HTTP error code (403, 404, ...)

from flask_sqlalchemy import SQLAlchemy
#   - SQLAlchemy: the ORM (Object-Relational Mapper). We write Python classes
#     (User, Product) and SQLAlchemy converts them into SQL for MySQL
#     automatically - we never write raw SQL for our own tables.

from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
#   - Flask-Login: handles sessions. Once a user logs in, Flask-Login keeps
#     them logged in across requests and exposes `current_user` everywhere.

from werkzeug.security import generate_password_hash, check_password_hash
#   - Werkzeug: hashes passwords so we NEVER store plain-text passwords.

from forms import (LoginForm, RegisterForm, ProductForm, SaleForm,
                   InventoryAdjustmentForm, ReceiveStockForm, InviteForm,
                   InviteAcceptForm)
#   - Our own WTForms definitions (email format, min password length, etc.)
from flask_migrate import Migrate          # DB schema migration tool (like git for tables)
from flask_wtf import CSRFProtect          # cross-site request forgery protection
from flask_wtf.csrf import CSRFError       # the exception raised when a CSRF token is bad
from dotenv import load_dotenv             # reads our .env config file
from datetime import datetime, timezone, timedelta  # timestamps + invitation expiry
from functools import wraps                # used by our role_required() decorator
import logging                             # Phase 4D: request + error logging

# Configure basic logging for production hardening
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
from decimal import Decimal                # exact money/quantity arithmetic (no float noise)
from sqlalchemy import func                # SQL functions (case-insensitive email matching)
import secrets                             # cryptographically secure invitation tokens
import os
import pymysql                             # pure-Python MySQL driver

# ------------------------- APP SETUP -------------------------
load_dotenv()  # loads .env file (keeps secrets out of the code)
db_url = os.getenv("DATABASE_URL")
if not db_url:
    raise RuntimeError("DATABASE_URL not found in .env file")

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret-change-in-prod'   # signs cookies/sessions
app.config['SQLALCHEMY_DATABASE_URI'] = db_url           # where MySQL lives
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False     # performance: no change notifications

import pymysql
pymysql.install_as_MySQLdb()
#   - Makes Flask-SQLAlchemy work with MySQL through the pymysql driver
#     (it normally expects the "MySQLdb" module which pymysql can impersonate).

db = SQLAlchemy(app)        # the ORM object - `db.Model`, `db.session`, `db.Column` come from here
migrate = Migrate(app, db)  # enables `flask db migrate` for schema changes

login_manager = LoginManager(app)
login_manager.login_view = 'login'
#   - Tells Flask-Login where to send users who hit a @login_required page.

# --- CSRF Protection ---
# FlaskForm already validates a CSRF token on any form using hidden_tag(),
# but CSRFProtect(app) extends that same protection to EVERY POST/PUT/PATCH/DELETE
# route app-wide, including plain HTML forms (like the delete button) that
# don't use a WTForm. This is the standard Flask-WTF approach.
csrf = CSRFProtect(app)

# ------------------------- MODELS (DATABASE TABLES) -------------------------
# Each class below = one MySQL table. Each attribute = one column.

class Shop(db.Model):
    """A retail business - the OWNERSHIP BOUNDARY of the whole system.
    Owner/Manager/Staff users belong to a Shop, and every Product belongs
    to a Shop, NOT to an individual employee. A shop's whole team works
    with the same products; different shops are fully isolated from each
    other (User.shop_id / Product.shop_id)."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    #   - Geographic location: used to filter KPDN market data to the shop's
    #     local region (e.g. Johor / Segamat) so the market median and ML
    #     features are hyper-localized. Both nullable because existing shops
    #     from earlier phases have no location data yet.
    state = db.Column(db.String(50), nullable=True)
    #   - Malaysian state (e.g. 'Johor', 'Selangor', 'W.P. Kuala Lumpur').
    district = db.Column(db.String(50), nullable=True)
    #   - District within the state (e.g. 'Segamah', 'Johor Bahru').
    #     Nullable: filtering falls back to state-level if district is NULL.
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    #   - users / products relationships come from backrefs on User and Product.


class User(UserMixin, db.Model):
    """Registered users with a role: owner / manager / staff.
    Every user belongs to exactly one Shop (shop_id).
    preferred_language stores the user's UI language choice for
    the 4-language localization system (EN/MS/ZH/TA)."""
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)  # never the raw password!
    role = db.Column(db.String(20), nullable=False, default='staff')  # owner | manager | staff | unassigned
    shop_id = db.Column(db.Integer, db.ForeignKey('shop.id'), nullable=True)
    preferred_language = db.Column(db.String(5), nullable=False, default='en')
    #   - UI language preference: 'en' (English), 'ms' (Bahasa Melayu),
    #     'zh' (Chinese/Mandarin), 'ta' (Tamil). Default is English.
    #   - FK to shop.id. NULL = an employee account that has NOT joined any
    #     shop yet (created via the "Join an existing shop" registration path).
    #     Shop registration creates a NEW shop and makes the registrant its
    #     owner (shop_id set). Invitation acceptance later assigns the invited
    #     employee's shop_id + role from the invitation row - never from a form.
    shop = db.relationship('Shop', backref='users')
    #   - `user.shop` -> the Shop object; `shop.users` -> this shop's team.
    #   - NOTE: no direct User->Product relationship any more. Products belong
    #     to the shop, so owner/manager/staff of one shop share its products.

    def can(self, *roles):
        """Convenience check: is this user allowed one of the given roles?
        Permission matrix (current phase):
            owner   -> full access (add/edit/delete products, shop data)
            manager -> add/edit/delete products
            staff   -> view only (dashboard + product pages)
        Future modules (sales, inventory...) will extend this matrix."""
        return self.role in roles

    def set_password(self, pw):
        # Hash before storing - generates a salted one-way hash.
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        # Compare a typed password against the stored hash.
        return check_password_hash(self.password_hash, pw)


class Product(db.Model):
    """A shop item the shop sells. This is the core business object.
    Product identity fields (name/brand/category/quantity/unit) exist so the
    product can later be matched against market data (PriceCatcher, online
    retail) WITHOUT requiring any external match - a product stands alone."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    #   - The shop's own name for the product (may NOT exist in PriceCatcher - that is fine).
    brand = db.Column(db.String(80), nullable=True)
    #   - Optional. Not every dry-good has a clear brand - allow 'Generic'/'Unbranded'.
    category = db.Column(db.String(80), nullable=True)
    #   - Optional - autocomplete fills it from PriceCatcher data; later ML can use it.
    quantity = db.Column(db.Numeric(10, 3), nullable=True)
    #   - Optional numeric amount (e.g. 1, 500, 1000). MUST be read together with `unit`:
    #     we store 1 + kg (never the string "1kg") so 1kg and 1000g can be normalized later.
    unit = db.Column(db.String(20), nullable=True)
    #   - Optional unit of measure: kg, g, L, ml, pcs, pack, box...
    cost_price = db.Column(db.Float, nullable=False)        # what the shop pays for it (RM)
    selling_price = db.Column(db.Numeric(10, 2), nullable=True)
    #   - What the shop CURRENTLY charges customers (RM). Different from suggested_price:
    #     selling_price = the price actually on the shelf; suggested_price = cost x (1 + margin%).
    #     Numeric(10,2) = fixed-point decimal - no floating-point noise for money.
    #     Optional: existing/legacy products may not have one yet (NULL).
    target_margin = db.Column(db.Float, nullable=False)     # percentage e.g. 30.0
    baseline_margin = db.Column(db.Float, nullable=True)    # margin locked at creation (PCAPA baseline)
    #   - baseline_margin is the compliance anchor: the margin "as originally set".
    #   - If a later edit pushes the margin ABOVE baseline without a cost rise,
    #     the system flags it as a profiteering risk under PCAPA 2011.
    is_price_controlled = db.Column(db.Boolean, default=False)
    #   - True if this product is a KPDN Barangan Kawalan (government price-controlled good).
    government_ceiling_price = db.Column(db.Float, nullable=True)
    #   - Official KPDN ceiling price (RM). The ML pricing engine will NEVER
    #     recommend above this value when is_price_controlled is True.
    shop_id = db.Column(db.Integer, db.ForeignKey('shop.id'), nullable=False)
    #   - FK to shop.id: the product belongs to the SHOP, not a user. This is
    #     what lets owner/manager/staff of one shop share the same products
    #     while different shops stay fully isolated (data isolation = filter
    #     every query by current_user.shop_id).
    shop = db.relationship('Shop', backref='products')
    #   - `product.shop` -> the owning Shop; `shop.products` -> the shop's stock.
    history = db.relationship('PriceHistory', backref='product', lazy=True,
                              cascade='all, delete-orphan',
                              order_by='PriceHistory.created_at')
    #   - Links each product to its full audit trail of price changes.
    #   - cascade='all, delete-orphan': deleting a product deletes its history rows too.

    @property
    def suggested_price(self):
        """THE PRICING ENGINE - computed live, not stored.
        Suggested selling price = cost price x (1 + margin%).
        Being a @property means it recalculates on every read, so it can
        never go stale - no separate column to maintain."""
        return round(self.cost_price * (1 + self.target_margin / 100), 2)

    @property
    def size_label(self):
        """Human-readable PACKAGE SIZE for display: quantity + unit
        (e.g. '1 kg', '5 pcs', '2 L'). This is NOT stock - inventory stock
        is shown separately as 'Current Stock'."""
        parts = []
        if self.quantity is not None:
            v = float(self.quantity)
            parts.append(str(int(v)) if v == int(v)
                         else ('%g' % v).rstrip('0').rstrip('.'))
        if self.unit:
            parts.append(self.unit)
        return ' '.join(parts) if parts else '—'


# -------------------------------------------------
# ShopInvitation - one row = an invitation to join a shop as manager/staff.
# Lifecycle: pending -> accepted | revoked | expired.
#   - token: cryptographically secure random string (secrets.token_urlsafe),
#     NEVER derivable from shop/email - the accept URL is the credential.
#   - email/role/shop_id are fixed AT INVITE TIME by the owner; the invitee
#     can never change them (the accept flow reads them from this row).
#   - expires_at = created_at + 48h; an expired invitation cannot be accepted.
#   - invited_by_user_id records which owner created it (auditability).
# -------------------------------------------------
INVITATION_TTL_HOURS = 48


class ShopInvitation(db.Model):
    """An owner's invitation for a manager/staff member to join their shop.
    The employee does NOT create a shop when accepting - they join the shop
    stored on this row. Membership is controlled entirely by the backend."""
    __tablename__ = 'shop_invitation'
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey('shop.id'), nullable=False)
    invited_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'),
                                   nullable=False)
    email = db.Column(db.String(120), nullable=False)   # who is invited
    role = db.Column(db.String(20), nullable=False)     # manager | staff (owners are NEVER invited)
    token = db.Column(db.String(128), unique=True, nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default='pending')
    #   - pending | accepted | revoked | expired
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime, nullable=False)
    shop = db.relationship('Shop', backref='invitations')
    invited_by = db.relationship('User', backref='created_invitations')

    @property
    def is_expired(self):
        # MySQL DATETIME columns come back timezone-naive; compare against
        # naive UTC so the 48h lifetime is consistent.
        return self.expires_at < datetime.utcnow()


# -------------------------------------------------
# Notification - one row = an in-app notification for one user
# (e.g. "ABC Mini Market invited you to join as Staff.").
#   - Created when an invitation targets an account that ALREADY exists, and
#     when an invited email later registers/logs in (sync_pending_invitations).
#   - is_read flips when the user opens the notifications page.
#   - type = 'shop_invitation' for now; the page renders Accept/Reject buttons
#     for pending-invitation notifications.
# -------------------------------------------------
class Notification(db.Model):
    __tablename__ = 'notification'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    type = db.Column(db.String(50), nullable=False)   # 'shop_invitation'
    title = db.Column(db.String(120), nullable=False)  # 'Shop Invitation'
    message = db.Column(db.String(255), nullable=False)  # 'X invited you to join as Staff.'
    invitation_id = db.Column(db.Integer, db.ForeignKey('shop_invitation.id'),
                              nullable=True)
    #   - nullable: points at the invitation that caused this notification.
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime,
                           default=lambda: datetime.now(timezone.utc))
    user = db.relationship('User', backref='notifications')
    invitation = db.relationship('ShopInvitation', backref='notifications')
# -------------------------------------------------


# -------------------------------------------------
# Price history log (cost/margin changes per product)
# Used for PCAPA-style margin-baseline tracking: every cost or margin
# change is recorded so an unexplained margin increase is visible.
# -------------------------------------------------
class PriceHistory(db.Model):
    """One row per change - the AUDIT TRAIL.
    The edit page shows the last 10 rows so every margin increase can be
    traced against a cost justification (or lack of one). Snapshot columns:
    cost, selling price (current price at that time) and margin - so old/new
    price and cost/margin changes are all traceable."""
    __tablename__ = 'price_history'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    cost_price = db.Column(db.Float, nullable=False)        # snapshot of cost at that time
    selling_price = db.Column(db.Numeric(10, 2), nullable=True)  # snapshot of current price (RM)
    target_margin = db.Column(db.Float, nullable=False)     # snapshot of margin at that time
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    #   - default lambda: set the timestamp at INSERT time (UTC).
# -------------------------------------------------


# -------------------------------------------------
# Sales - one row per completed sale (a quantity of ONE product sold at
# a particular per-unit price and time).
# -------------------------------------------------
class Sale(db.Model):
    """One row = a quantity of one Product sold at a per-unit price at a time.
    selling_price is a SNAPSHOT of the price at the moment of sale - the
    product's current price can change later without rewriting history.
    Revenue is NOT stored: revenue = quantity x selling_price, calculated
    on demand."""
    __tablename__ = 'sale'
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey('shop.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Numeric(10, 3), nullable=False)      # units sold (kg/pcs/...)
    selling_price = db.Column(db.Numeric(10, 2), nullable=False)  # per-unit price AT SALE TIME (RM)
    sold_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    shop = db.relationship('Shop', backref='sales')
    product = db.relationship('Product', backref='sales')
    #   - NOTE: no cascade here ON PURPOSE. A product that has historical sales
    #     must NOT be hard-deleted (the delete route blocks it); the FK itself
    #     also refuses to delete a product that still has sale rows.


# -------------------------------------------------
# Inventory - one record per product per shop: the stock level currently
# believed to be on hand. current_stock is NOT the same as Product.quantity
# (product.quantity = amount inside one package; current_stock = how many
# sellable units are available).
# -------------------------------------------------
class Inventory(db.Model):
    """Current stock for a shop's product, with a minimum reorder level.
    One row per product (product_id is unique). Stock is only ever changed
    through explicit adjustments or recorded sales - the system never invents
    stock levels."""
    __tablename__ = 'inventory'
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey('shop.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'),
                           unique=True, nullable=False)
    current_stock = db.Column(db.Numeric(10, 3), nullable=False, default=0)
    minimum_stock = db.Column(db.Numeric(10, 3), nullable=False, default=0)
    updated_at = db.Column(db.DateTime,
                           default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))
    shop = db.relationship('Shop', backref='inventory')
    product = db.relationship('Product',
                              backref=db.backref('inventory', uselist=False,
                                                 cascade='all, delete-orphan'))
    #   - cascade on product delete: inventory is product-bound operational
    #     data, so it goes with the product (but only when the product has NO
    #     sales - the delete route refuses that case first).


# -------------------------------------------------
# InventoryAdjustment - a manual stock change with a reason, so inventory
# movements stay traceable (+20 "Stock received", -5 "Damaged", ...).
# No purchase orders / suppliers / warehouses / batches in this phase.
# -------------------------------------------------
class InventoryAdjustment(db.Model):
    """One row per MANUAL stock adjustment (receive / correction).
    Sale-driven stock decreases are NOT logged here - the sale row itself is
    the trace for those. quantity_change is positive for stock-in and
    negative for stock-out; user_id records WHO made the change (audit)."""
    __tablename__ = 'inventory_adjustment'
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey('shop.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity_change = db.Column(db.Numeric(10, 3), nullable=False)  # +20 / -5
    reason = db.Column(db.String(200), nullable=False)              # why the change happened
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    #   - who performed the adjustment (nullable: legacy rows have no user).
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    shop = db.relationship('Shop', backref='inventory_adjustments')
    product = db.relationship('Product',
                              backref=db.backref('inventory_adjustments',
                                                 cascade='all, delete-orphan'))
    user = db.relationship('User', backref='inventory_adjustments')
# -------------------------------------------------


# -------------------------------------------------
# PriceCatcher lookup tables (from the government KPDN dataset)
# These tables are imported by our teammate's import script and are
# READ-ONLY reference data - the backend queries them to power the
# product-name autocomplete.
# -------------------------------------------------
class LookupItem(db.Model):
    """Master list of ~406 official PriceCatcher items (e.g. 'BERAS CAP JASMINE').
    Fresh produce (BARANGAN SEGAR) and ready-to-cook meals (MAKANAN SIAP MASAK)
    are excluded at import - the shop does not stock those."""
    __tablename__ = 'lookup_item'
    item_code   = db.Column(db.String(50), primary_key=True)
    item        = db.Column(db.String(255), nullable=False)   # product name
    unit        = db.Column(db.String(50))
    item_group  = db.Column(db.String(100))
    item_category = db.Column(db.String(100))   # used for autofill into the Category field


class LookupPremise(db.Model):
    """Reference list of shops/outlets that report prices to PriceCatcher."""
    __tablename__ = 'lookup_premise'
    premise_code = db.Column(db.String(50), primary_key=True)
    premise      = db.Column(db.String(255), nullable=False)   # shop/outlet name
    address      = db.Column(db.String(255))
    premise_type = db.Column(db.String(50))
    state        = db.Column(db.String(50))
    district     = db.Column(db.String(50))


class Price(db.Model):
    """Actual market prices reported per item per premise per date.
    (Anchoring suggested prices to real market data is a future milestone.)"""
    __tablename__ = 'price'
    price_id    = db.Column(db.Integer, primary_key=True, autoincrement=True)
    date        = db.Column(db.Date, nullable=False)
    price       = db.Column(db.Numeric(10, 2), nullable=False)     # Numeric = exact decimals (money!)
    item_code   = db.Column(db.String(50), db.ForeignKey('lookup_item.item_code'), nullable=False)
    premise_code= db.Column(db.String(50), db.ForeignKey('lookup_premise.premise_code'), nullable=False)


class PriceCatcherItem(db.Model):
    """Denormalized copy of lookup_item with a surrogate `id` PK.
    Contains ALL lookup items (including ones with no rows in the `price`
    table — market data is optional, not a membership requirement).
    Populated by import_pricecatcher.py. Lets the app reference items by
    a single integer id instead of the string item_code."""
    __tablename__ = 'price_catcher_item'
    id            = db.Column(db.Integer, primary_key=True, autoincrement=True)
    item_code     = db.Column(db.String(60), unique=True, nullable=False)
    item          = db.Column(db.String(255), nullable=False)
    unit          = db.Column(db.String(50))
    item_group    = db.Column(db.String(100))
    item_category = db.Column(db.String(100))
# -------------------------------------------------


# -------------------------------------------------
# PHASE 3A - MARKET DATA FOUNDATION
#
# External market data (PriceCatcher, online retailers, manual entry)
# lives in its OWN tables - it is NOT bolted onto the shop Product.
# This keeps Product source-independent (a shop product stands alone
# even when no market match exists). The two sides meet only through
# ProductMarketMatch, a mapping table with a confidence score.
#
#   market_source  ->  market_item  ->  market_price_observation
#                                             |
#   product (shop) <------ ProductMarketMatch -+   (match table)
# -------------------------------------------------


class MarketSource(db.Model):
    """A named source of market price data.
    E.g. 'PriceCatcher' (government), 'Lotus Online' (online retailer),
    or a manually-typed observation. source_type is one of:
    government | online_retailer | manual."""
    __tablename__ = 'market_source'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)          # e.g. 'PriceCatcher'
    source_type = db.Column(db.Enum('government', 'online_retailer', 'manual'),
                            nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    #   - is_active lets an import job be switched off without deleting rows.
    items = db.relationship('MarketItem', backref='source')
    #   - source.items -> the MarketItems scraped/imported from this source.


class MarketItem(db.Model):
    """ONE product listing from an external source (a row in that source's
    catalogue). raw_title is exactly what the source said; normalized_title
    is the cleaned form used for matching. package_quantity/package_unit
    describe the size of one package (same convention as Product.quantity/
    unit) - NEVER the stock of any shop."""
    __tablename__ = 'market_item'
    id = db.Column(db.Integer, primary_key=True)
    source_id = db.Column(db.Integer, db.ForeignKey('market_source.id'),
                          nullable=False)
    external_id = db.Column(db.String(100), nullable=True)
    #   - the source's own SKU / native id (PriceCatcher item_code, ...).
    raw_title = db.Column(db.String(255), nullable=False)      # as published
    normalized_title = db.Column(db.String(255), index=True)   # clean_text()
    brand = db.Column(db.String(100), nullable=True)
    category = db.Column(db.String(100), nullable=True)
    package_quantity = db.Column(db.Numeric(10, 3), nullable=False)  # e.g. 1 / 500 / 2.5
    package_unit = db.Column(db.String(20), nullable=False)          # e.g. kg / g / L / ml / pcs
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime,
                           default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))
    observations = db.relationship('MarketPriceObservation',
                                   backref='market_item',
                                   cascade='all, delete-orphan')
    #   - deleting a MarketItem deletes its price observations too.


class MarketPriceObservation(db.Model):
    """ONE observed price for a MarketItem at a point in time.

    This is the time-series table that holds historical price observations
    from external sources. Each row represents a single price point for
    a market item at a specific date.

    effective_price is DERIVED on insert: promo_price when is_on_promo,
    else regular_price - callers never pass it directly. This derivation
    ensures consistency: the effective price always reflects the best
    available price (promo or regular).

    normalized_unit_price is the price per BASE unit (RM/kg, RM/L, RM/unit)
    computed with utils.normalization.calculate_unit_price. This is the
    number that makes fair comparisons across differently-sized packages
    possible (e.g. a 500g packet vs a 10kg bag)."""
    __tablename__ = 'market_price_observation'
    id = db.Column(db.Integer, primary_key=True)
    market_item_id = db.Column(db.Integer, db.ForeignKey('market_item.id'),
                               nullable=False)
    regular_price = db.Column(db.Numeric(10, 2), nullable=False)   # RM
    promo_price = db.Column(db.Numeric(10, 2), nullable=True)      # RM (NULL = no promo)
    is_on_promo = db.Column(db.Boolean, nullable=False, default=False)
    effective_price = db.Column(db.Numeric(10, 2), nullable=False)  # derived, see __init__
    normalized_unit_price = db.Column(db.Numeric(10, 4), nullable=False)  # RM per base unit
    #   - Geographic columns for localized market intelligence. The ETL
    #     populates these from the premise's lookup_premise row so the
    #     market_analysis service can filter by the shop's state/district.
    #     Nullable for backwards compatibility with pre-geographic data.
    state = db.Column(db.String(50), nullable=True, index=True)
    district = db.Column(db.String(50), nullable=True)
    observed_at = db.Column(db.DateTime,
                            default=lambda: datetime.now(timezone.utc),
                            index=True)

    def __init__(self, **kw):
        # Derive effective_price: promo wins when the item is on promo.
        if kw.get('effective_price') is None:
            kw['effective_price'] = (kw.get('promo_price')
                                     if kw.get('is_on_promo')
                                     else kw.get('regular_price'))
        # Derive normalized_unit_price from the linked MarketItem's package
        # size when the caller did not supply one.
        if kw.get('normalized_unit_price') is None:
            mi = kw.get('market_item')
            if mi is not None and mi.package_quantity:
                from utils.normalization import calculate_unit_price
                kw['normalized_unit_price'] = calculate_unit_price(
                    float(kw['effective_price']),
                    float(mi.package_quantity), mi.package_unit)
        super().__init__(**kw)


class ProductMarketMatch(db.Model):
    """The ONLY bridge between a shop Product and a MarketItem.

    This mapping table is the architectural keystone of the Market
    Intelligence system. It keeps Products source-independent (a product
    stands alone even with no market match) while enabling market data
    integration when matches are established.

    A product may be matched to many market items (or none); a market
    item may be matched to many shop products (each shop has its own).

    confidence_score = how sure the matcher is (0.95 = 95%)
    match_type = exact | fuzzy | manual
    is_verified = shop owner explicitly confirmed the link
    is_rejected = shop owner explicitly rejected the suggestion
    """
    __tablename__ = 'product_market_match'
    __table_args__ = (db.UniqueConstraint('shop_product_id', 'market_item_id',
                                          name='uq_product_market_match'),)
    #   - a shop product can never link the same market item twice.
    id = db.Column(db.Integer, primary_key=True)
    shop_product_id = db.Column(db.Integer, db.ForeignKey('product.id'),
                                nullable=False)
    market_item_id = db.Column(db.Integer, db.ForeignKey('market_item.id'),
                               nullable=False)
    confidence_score = db.Column(db.Numeric(3, 2), nullable=True)  # 0.00 - 1.00
    match_type = db.Column(db.Enum('exact', 'fuzzy', 'manual'), nullable=False)
    is_verified = db.Column(db.Boolean, nullable=False, default=False)
    #   - True only after the SHOP OWNER/MANAGER explicitly confirms the link.
    is_rejected = db.Column(db.Boolean, nullable=False, default=False)
    #   - True after the user rejected a suggestion, so it never reappears.
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    shop_product = db.relationship('Product',
                                   backref=db.backref('market_matches',
                                                      cascade='all, delete-orphan'))
    market_item = db.relationship('MarketItem', backref='product_matches')
# -------------------------------------------------

# Phase 3C/3D services. Imported HERE (not at the top of the file)
# because they import `app` back - at this point the module has fully
# defined db + every model they need, so the circular import resolves
# safely.
from services.matching import apply_suggestions        # noqa: E402
from services.market_analysis import get_market_stats   # noqa: E402
from services.pricing_engine import (get_price_recommendation,  # noqa: E402
                                     apply_price as _apply_price)
from services.dashboard_service import get_dashboard_metrics  # noqa: E402
from translations import _t, LANGUAGE_NAMES, LANGUAGE_OPTIONS  # noqa: E402


# -------------------------------------------------
# API ROUTE - Autocomplete for PriceCatcher items
# The frontend calls GET /autocomplete?q=beras when the user types in the
# product-name field. This route returns matching official items as JSON.
#
# The lookup runs against price_catcher_item (NOT lookup_item): the
# denormalized copy of lookup_item with a surrogate id, built by
# import_pricecatcher.py. All 406 items are searchable - having market
# price data is not required to appear in autocomplete.
# -------------------------------------------------
@app.route('/autocomplete')
@login_required  # only logged-in users may use the lookup
def autocomplete():
    """
    Return up to 10 items from price_catcher_item whose 'item' column starts
    with the supplied term (case-insensitive). Each result includes the fields
    needed to fill the product name and category inputs.
    """
    from sqlalchemy import func

    term = request.args.get('q', '').strip()
    if not term or len(term) < 2:
        # Too short - return empty list so the JS hides the dropdown
        return jsonify([])

    # Case-insensitive prefix search:
    #   LIKE 'beras%' with both sides lowercased -> matches "BERAS" too.
    matches = (PriceCatcherItem.query
                   .filter(func.lower(PriceCatcherItem.item).like(func.lower(term) + '%'))
                   .order_by(PriceCatcherItem.item)
                   .limit(10)
                   .all())

    # Build JSON payload
    results = []
    for m in matches:
        results.append({
            'item_code': m.item_code,
            'item': m.item,                     # -> Product Name field
            'item_category': m.item_category,   # -> Category field
            'unit': m.unit                      # optional, for future use
        })

    return jsonify(results)
# -------------------------------------------------


# ------------------------- AUTH HELPERS -------------------------
@login_manager.user_loader
def load_user(uid):
    """Flask-Login calls this on every request to reload the logged-in user
    from the session cookie. Returning None = not logged in."""
    return User.query.get(int(uid))


# -------------------------------------------------
# Role-based access control
# -------------------------------------------------
def role_required(*roles):
    """Decorator: restrict a view to users with one of the given roles.
    Current permission matrix:
        owner   -> full product access (add/edit/delete) + shop-level data
        manager -> add/edit/delete products
        staff   -> view only (no add/edit/delete)
    Usage (order matters - login_required OUTSIDE so guests are redirected
    to the login page, while a wrong role gets a 403):
        @app.route('/admin')
        @login_required
        @role_required('owner')
        def admin(): ...
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(403)
            if not current_user.can(*roles):
                flash('You do not have permission to access that page.', 'danger')
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator
# -------------------------------------------------


# ------------------------- TEMPLATE FILTERS -------------------------
@app.template_filter('fmt_qty')
def fmt_qty(value):
    """Format a Numeric quantity nicely: 2.000 -> 2, 0.500 -> 0.5.
    Quantities are stored as Decimal(10,3); showing '2.000' in tables is ugly."""
    if value is None:
        return '—'
    v = float(value)
    if v == int(v):
        return str(int(v))
    return ('%g' % v).rstrip('0').rstrip('.')
# -------------------------------------------------


# ------------------------- NOTIFICATION HELPERS -------------------------
def norm_email(email):
    """Canonical email form: trimmed + lowercased. AHMAD@X.COM and ahmad@x.com
    are the SAME identity - every lookup compares case-insensitively."""
    return (email or '').strip().lower()


def sync_pending_invitations(user):
    """Surface every pending invitation targeting `user`'s email as an in-app
    Notification. Idempotent (never duplicates) and lazily expires overdue
    invitations. The CALLER must commit."""
    email = norm_email(user.email)
    pendings = (ShopInvitation.query
                .filter(func.lower(ShopInvitation.email) == email,
                        ShopInvitation.status == 'pending')
                .all())
    for inv in pendings:
        if inv.is_expired:
            inv.status = 'expired'
            continue
        already = Notification.query.filter_by(user_id=user.id,
                                               invitation_id=inv.id).first()
        if already is None:
            db.session.add(Notification(
                user_id=user.id,
                type='shop_invitation',
                title='Shop Invitation',
                message=f'{inv.shop.name} invited you to join as '
                        f'{inv.role.capitalize()}.',
                invitation_id=inv.id))


def mark_invitation_notifications_read(inv):
    """Mark every notification linked to one invitation as read."""
    for n in Notification.query.filter_by(invitation_id=inv.id).all():
        n.is_read = True


@app.context_processor
def inject_unread_notifications():
    """Expose the unread-notification count and translation helpers to every
    template. The navbar uses these for localized UI text and the 🔔 badge."""
    if current_user.is_authenticated:
        lang = getattr(current_user, 'preferred_language', 'en') or 'en'
        return {
            'unread_notifications': Notification.query.filter_by(
                user_id=current_user.id, is_read=False).count(),
            'current_language': lang,
            'language_names': LANGUAGE_NAMES,
            'language_options': LANGUAGE_OPTIONS,
            '_t': lambda key: _t(key, lang),
        }
    return {
        'unread_notifications': 0,
        'current_language': 'en',
        'language_names': LANGUAGE_NAMES,
        'language_options': LANGUAGE_OPTIONS,
        '_t': lambda key: _t(key, 'en'),
    }
# -------------------------------------------------


# ------------------------- ROUTES -------------------------
# Each @app.route maps a URL to a Python function (a "view").

@app.route('/')
def home():
    # The landing page simply forwards to the login screen.
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    """Create a new account. GET = show the form, POST = process it."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))   # already logged in? skip registration

    form = RegisterForm()
    if form.validate_on_submit():
        # validate_on_submit() = the browser sent a POST AND every WTForms
        # validator passed (valid email, password >= 6 chars, CSRF token OK).
        email = norm_email(form.email.data)   # AHMAD@X.COM == ahmad@x.com
        if User.query.filter(func.lower(User.email) == email).first():
            flash('Email already registered', 'danger')
        elif form.account_type.data == 'shop':
            # PATH A - CREATE A NEW SHOP: creates the shop AND the registrant
            # as its OWNER. There is no public role dropdown - the owner role
            # is derived from this path, never chosen.
            # Geographic location: state and district drive localized market
            # data filtering in the Market Analysis Engine (Phase 3D).
            shop_state = (form.state.data or '').strip() or None
            shop_district = (form.district.data or '').strip() or None
            shop = Shop(name=form.shop_name.data,
                        state=shop_state, district=shop_district)
            db.session.add(shop)
            db.session.flush()               # obtain shop.id for the new user
            u = User(email=email, role='owner', shop_id=shop.id)
            u.set_password(form.password.data)   # hash the password before saving
            db.session.add(u)                    # queue the insert
            db.session.commit()                  # write shop + user to MySQL
            flash('Shop created! Please log in.', 'success')
            return redirect(url_for('login'))
        else:
            # PATH B - JOIN AN EXISTING SHOP (employee account): the account is
            # created WITHOUT any shop membership (shop_id NULL, role
            # 'unassigned'). The OWNER decides the final role via an invitation
            # - the employee must explicitly ACCEPT it later. No shop is ever
            # created here, and no role is taken from the form.
            u = User(email=email, role='unassigned', shop_id=None)
            u.set_password(form.password.data)   # hash the password before saving
            db.session.add(u)
            db.session.flush()               # obtain u.id for notifications
            sync_pending_invitations(u)      # surface any existing pending invite
            db.session.commit()
            flash('Account created! If you have a pending invitation, accept it '
                  'from the bell icon (Notifications).', 'success')
            return redirect(url_for('login'))
    return render_template('register.html', form=form)


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Verify email + password, then start a Flask-Login session."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    form = LoginForm()
    if form.validate_on_submit():
        # Emails match case-insensitively (AHMAD@X.COM == ahmad@x.com).
        u = User.query.filter(
            func.lower(User.email) == norm_email(form.email.data)).first()
        if u and u.check_password(form.password.data):
            # check_password hashes the typed password and compares to the stored hash.
            login_user(u, remember=form.remember.data)   # remember = persistent cookie
            # If an invitation was created for this email while the user was
            # logged out (or before the account existed), surface it now.
            sync_pending_invitations(u)
            db.session.commit()
            # Safe next-page redirect (used by the invitation flow): only
            # allow same-site relative paths - never external URLs (open-
            # redirect protection).
            next_page = request.args.get('next')
            if next_page and next_page.startswith('/') and not next_page.startswith('//'):
                return redirect(next_page)
            return redirect(url_for('dashboard'))
        flash('Invalid credentials', 'danger')           # generic message = don't leak which part was wrong
    return render_template('login.html', form=form)


@app.route('/logout')
@login_required
def logout():
    logout_user()                    # clear the Flask-Login session
    return redirect(url_for('login'))


# -------------------------------------------------
# USER PROFILE & SETTINGS
# -------------------------------------------------
@app.route('/profile')
@login_required
def profile():
    """User profile page showing account details, role badge, and shop info."""
    return render_template('profile.html', user=current_user)


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    """Tabbed settings page for account details, shop settings, and language."""
    active_tab = request.args.get('tab', 'account')

    # --- Account Details Form ---
    account_form = UserProfileForm(obj=current_user)
    shop_form = ShopSettingsForm(obj=current_user.shop if current_user.shop else None)
    prefs_form = PreferencesForm(obj=current_user)

    if request.method == 'POST':
        form_type = request.form.get('form_type')

        if form_type == 'account' and account_form.validate_on_submit():
            email = norm_email(account_form.email.data)
            existing = User.query.filter(
                func.lower(User.email) == email,
                User.id != current_user.id).first()
            if existing:
                flash('That email is already in use by another account.', 'danger')
            else:
                current_user.email = email
                if account_form.new_password.data:
                    current_user.set_password(account_form.new_password.data)
                db.session.commit()
                flash('Account details updated.', 'success')
            active_tab = 'account'

        elif form_type == 'shop' and current_user.can('owner'):
            if shop_form.validate_on_submit():
                if current_user.shop:
                    current_user.shop.name = shop_form.shop_name.data.strip()
                    current_user.shop.state = (shop_form.state.data or '').strip() or None
                    current_user.shop.district = (shop_form.district.data or '').strip() or None
                    if shop_form.default_target_margin.data is not None:
                        # Store as a note; actual enforcement is per-product.
                        current_user.shop._default_margin = shop_form.default_target_margin.data
                    db.session.commit()
                    flash('Shop settings updated.', 'success')
                else:
                    flash('No shop associated with your account.', 'danger')
            active_tab = 'shop'

        elif form_type == 'language' and prefs_form.validate_on_submit():
            lang = prefs_form.preferred_language.data
            if lang in ('en', 'ms', 'zh', 'ta'):
                current_user.preferred_language = lang
                db.session.commit()
                flash('Language preference saved.', 'success')
            else:
                flash('Invalid language selection.', 'danger')
            active_tab = 'language'

    return render_template('settings.html',
                           user=current_user,
                           account_form=account_form,
                           shop_form=shop_form,
                           prefs_form=prefs_form,
                           active_tab=active_tab)


@app.route('/dashboard')
@login_required
def dashboard():
    # Unassigned employee account ("Join an existing shop" path): no shop
    # membership yet, so there is nothing to list - point them at their
    # notifications where any pending invitation lives.
    if current_user.shop_id is None:
        return render_template('dashboard.html', products=[], inv_map={},
                               unassigned=True, metrics=None)
    # DATA ISOLATION: products are owned by the SHOP, not the user. Every
    # member of a shop (owner/manager/staff) sees the SAME products; users
    # of other shops never see them. This filter is the whole security model.
    products = Product.query.filter_by(shop_id=current_user.shop_id).all()
    # Map product_id -> inventory row so the table can show current stock.
    inv_map = {i.product_id: i for i in
               Inventory.query.filter_by(shop_id=current_user.shop_id).all()}
    # Phase 4A: compute dashboard metrics and action items
    metrics = get_dashboard_metrics(current_user.shop_id)
    return render_template('dashboard.html', products=products, inv_map=inv_map,
                           metrics=metrics)


@app.route('/product/new', methods=['GET', 'POST'])
@login_required
@role_required('owner', 'manager')  # staff may NOT add products
def new_product():
    form = ProductForm()
    if form.validate_on_submit():
        # The shop_id always comes from the logged-in user - NEVER from the
        # submitted form (an attacker cannot re-point a product at another shop).
        # --- data-quality guardrails (Phase 4B) ---
        qty = form.quantity.data
        if qty is not None:
            try:
                qty = float(qty)
                if qty <= 0:
                    qty = None  # reject zero / negative as 'not set'
            except (ValueError, TypeError):
                qty = None     # non-numeric input treated as blank
        unit_raw = (form.unit.data or '').strip()
        # Strip stray digits from the unit field (e.g. user types "500g" there)
        unit_clean = ''.join(c for c in unit_raw if not c.isdigit()).strip() or None
        p = Product(
            name=form.name.data,
            brand=(form.brand.data or '').strip() or None,
            category=(form.category.data or '').strip() or None,
            quantity=qty,
            unit=unit_clean,
            cost_price=form.cost_price.data,
            selling_price=form.selling_price.data,
            target_margin=form.target_margin.data,
            is_price_controlled=form.is_price_controlled.data or False,
            government_ceiling_price=form.government_ceiling_price.data if form.is_price_controlled.data else None,
            shop_id=current_user.shop_id   # product belongs to the shop
        )
        p.baseline_margin = p.target_margin  # lock the margin baseline at creation
        #   - This is the PCAPA anchor: the margin "as originally set".
        #     Later edits compare against it.
        db.session.add(p)
        db.session.flush()  # obtain p.id for the history row
        db.session.add(PriceHistory(
            product_id=p.id,
            cost_price=p.cost_price,
            selling_price=p.selling_price,
            target_margin=p.target_margin
        ))
        #   - Record the very first state so the audit trail starts at day one.
        # New products start with an inventory record at ZERO stock - the shop
        # adjusts stock when real stock is known. The system never invents stock.
        db.session.add(Inventory(
            shop_id=p.shop_id,
            product_id=p.id,
            current_stock=0,
            minimum_stock=0
        ))
        # Phase 3C: auto-suggest market matches right after creation so the
        # new product's Market Intelligence tab has candidates immediately.
        # Suggestions are ALWAYS unverified - only the owner/manager can
        # confirm them. Synchronous because the catalogue is ~400 items.
        apply_suggestions(p)
        db.session.commit()
        flash('Product added!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('product_form.html', form=form, title='Add Product',
                           baseline_margin=None, prev_cost=None, history=None)


@app.route('/product/<int:pid>/edit', methods=['GET', 'POST'])
@login_required
@role_required('owner', 'manager')  # staff may NOT edit products
def edit_product(pid):
    p = Product.query.get_or_404(pid)
    if p.shop_id != current_user.shop_id:
        # SHOP OWNERSHIP CHECK: a user may only edit products of their OWN
        # shop. Cross-shop access is rejected even if the URL id is valid -
        # never trust the URL product id alone. (Role was already enforced
        # by role_required above; this enforces the shop boundary.)
        abort(403)

    form = ProductForm(obj=p)   # pre-fill the form with the product's current values
    if form.validate_on_submit():
        old_cost = p.cost_price
        old_margin = p.target_margin
        old_selling = p.selling_price
        new_cost = form.cost_price.data
        new_margin = form.target_margin.data
        new_selling = form.selling_price.data

        # Products created before baseline tracking existed: adopt the current
        # margin as their baseline (there is no older data to reconstruct).
        if p.baseline_margin is None:
            p.baseline_margin = old_margin

        form.populate_obj(p)    # copy the validated form values onto the Product
        # If price control is unchecked, clear the ceiling price.
        if not p.is_price_controlled:
            p.government_ceiling_price = None
        # Normalize optional text fields: blank input -> NULL (not empty string).
        p.brand = (p.brand or '').strip() or None
        # --- data-quality guardrails (Phase 4B) ---
        if p.quantity is not None:
            try:
                p.quantity = float(p.quantity)
                if p.quantity <= 0:
                    p.quantity = None
            except (ValueError, TypeError):
                p.quantity = None
        unit_raw = (p.unit or '').strip()
        p.unit = ''.join(c for c in unit_raw if not c.isdigit()).strip() or None
        p.category = (p.category or '').strip() or None

        # Log every cost/margin/selling-price change - the visible "cost history" trail.
        # Only writes a row when a value ACTUALLY changed, so saving without
        # editing doesn't spam the history table.
        if (new_cost != old_cost or new_margin != old_margin
                or new_selling != old_selling):
            db.session.add(PriceHistory(
                product_id=p.id,
                cost_price=new_cost,
                selling_price=new_selling,
                target_margin=new_margin
            ))

        # PCAPA-style compliance check: raising the margin beyond baseline with
        # no cost increase is an *unexplained* margin increase -> profiteering risk.
        # (The frontend also warns live with JS; this server check is the authority.)
        if new_margin > p.baseline_margin and new_cost <= old_cost:
            flash(
                f'\u26a0\ufe0f Compliance warning: margin raised from {p.baseline_margin:g}% to '
                f'{new_margin:g}% with no cost increase. Under the Price Control and '
                f'Anti-Profiteering Act 2011, only a cost increase justifies a higher '
                f'margin \u2014 an unexplained increase is treated as profiteering risk.',
                'warning'
            )

        # Phase 3C: refresh market suggestions when the product is edited
        # (name/package/category may have changed). Verified and rejected
        # links are preserved - only stale suggestions refresh.
        apply_suggestions(p)

        db.session.commit()
        flash('Product updated!', 'success')
        return redirect(url_for('dashboard'))

    # The 10 most recent history rows, newest first, shown on the edit page.
    history = (PriceHistory.query.filter_by(product_id=p.id)
               .order_by(PriceHistory.created_at.desc()).limit(10).all())
    return render_template('product_form.html', form=form, title='Edit Product',
                           baseline_margin=p.baseline_margin, prev_cost=p.cost_price,
                           history=history)


@app.route('/product/<int:pid>/delete', methods=['POST'])
@login_required
@role_required('owner', 'manager')  # staff may NOT delete products
def delete_product(pid):
    p = Product.query.get_or_404(pid)
    if p.shop_id != current_user.shop_id:
        # Same shop-ownership check as edit - defense in depth.
        abort(403)
    if Sale.query.filter_by(product_id=p.id).first():
        # A product that has historical sales must NOT be hard-deleted - that
        # would destroy business history. The shop can edit it instead.
        flash('Cannot delete: this product has sales history. '
              'Delete is blocked to preserve historical records.', 'danger')
        return redirect(url_for('dashboard'))
    db.session.delete(p)      # cascades its PriceHistory / Inventory / adjustments
    db.session.commit()
    flash('Product deleted', 'success')
    return redirect(url_for('dashboard'))


# -------------------------------------------------
# PHASE 3C - PRODUCT DETAIL PAGE + MARKET INTELLIGENCE API
# -------------------------------------------------
def _market_state(product, shop=None):
    """(verified, suggested) display dicts for a product's match tab.
    Each dict carries the ProductMarketMatch row, its MarketItem, and
    (for verified links) a market-price summary from the observations.

    When a shop with geographic data is provided, observations are
    filtered by the shop's state/district (with fallback to state
    and then national) so the verified market links show the most
    relevant local pricing information.
    """
    def obs_summary(mi):
        # Build the base query for this market item's observations.
        query = (MarketPriceObservation.query
                 .filter_by(market_item_id=mi.id))

        # Apply geographic filtering when the shop has location data.
        # This mirrors the 3-tier fallback used in get_market_stats.
        if shop and getattr(shop, 'district', None) and getattr(shop, 'state', None):
            # Tier 1: Try district-level.
            district_rows = (query.filter_by(
                state=shop.state, district=shop.district
            ).order_by(MarketPriceObservation.observed_at.asc()).all())
            if len(district_rows) >= 3:
                rows = district_rows
            else:
                # Tier 2: Fall back to state-level.
                state_rows = (query.filter_by(
                    state=shop.state
                ).order_by(MarketPriceObservation.observed_at.asc()).all())
                if len(state_rows) >= 3:
                    rows = state_rows
                else:
                    # Tier 3: National fallback.
                    rows = (query.order_by(
                        MarketPriceObservation.observed_at.asc()).all())
        elif shop and getattr(shop, 'state', None):
            # Only state is available — skip district tier.
            state_rows = (query.filter_by(
                state=shop.state
            ).order_by(MarketPriceObservation.observed_at.asc()).all())
            if len(state_rows) >= 3:
                rows = state_rows
            else:
                rows = (query.order_by(
                    MarketPriceObservation.observed_at.asc()).all())
        else:
            # No location data — show all observations (national).
            rows = query.order_by(MarketPriceObservation.observed_at.asc()).all()

        if not rows:
            return None
        prices = [float(r.regular_price) for r in rows]
        return {
            'latest': round(prices[-1], 2),
            'min': round(min(prices), 2),
            'max': round(max(prices), 2),
            'avg': round(sum(prices) / len(prices), 2),
            'unit_price': float(rows[-1].normalized_unit_price),
            'count': len(rows),
            'first_date': rows[0].observed_at.date().isoformat(),
            'last_date': rows[-1].observed_at.date().isoformat(),
        }

    verified, suggested = [], []
    for m in product.market_matches:
        if m.is_verified:
            verified.append({'match_id': m.id, 'item': m.market_item,
                             'confidence': m.confidence_score,
                             'match_type': m.match_type, 'verified': True,
                             'summary': obs_summary(m.market_item)})
        elif not m.is_rejected:
            suggested.append({'match_id': m.id, 'item': m.market_item,
                              'confidence': m.confidence_score,
                              'match_type': m.match_type, 'verified': False,
                              'summary': None})
    suggested.sort(key=lambda s: float(s['confidence'] or 0), reverse=True)
    return verified, suggested


def _serialize_match(s):
    """Convert a _market_state() dict into a JSON-safe dict for the API."""
    it = s['item']
    qty = float(it.package_quantity)
    pkg = f"{str(int(qty)) if qty == int(qty) else ('%g' % qty).rstrip('0').rstrip('.')} {it.package_unit}".strip()
    return {
        'match_id': s['match_id'],
        'market_item_id': it.id,
        'title': it.raw_title,
        'package': pkg,
        'category': it.category,
        'source': it.source.name if it.source else None,
        'confidence': (float(s['confidence'])
                       if s['confidence'] is not None else None),
        'match_type': s['match_type'],
        'verified': s['verified'],
        'summary': s['summary'],
    }


def _market_json(product):
    """JSON payload of a product's full match state (verified + suggested)
    plus the Phase 3D market statistics - so the frontend can refresh the
    Market Summary card after every verify/reject/remove/search action."""
    verified, suggested = _market_state(product, product.shop)
    return jsonify({'verified': [_serialize_match(s) for s in verified],
                    'suggested': [_serialize_match(s) for s in suggested],
                    'stats': get_market_stats(product.id,
                                              product.shop)})


@app.route('/api/product/<int:pid>/market-stats', methods=['GET'])
@login_required
def api_product_market_stats(pid):
    """Phase 3D: market statistics for a product's verified matches.
    All shop roles can read; cross-shop is blocked like every other route."""
    p = Product.query.get_or_404(pid)
    if p.shop_id != current_user.shop_id:
        abort(403)
    return jsonify(get_market_stats(p.id, p.shop))


@app.route('/product/<int:pid>')
@login_required
def product_detail(pid):
    """Product details + Market Intelligence hub (all shop roles can view)."""
    p = Product.query.get_or_404(pid)
    if p.shop_id != current_user.shop_id:
        # SHOP OWNERSHIP CHECK: never trust the URL product id alone.
        abort(403)
    inv = Inventory.query.filter_by(product_id=p.id).first()
    history = (PriceHistory.query.filter_by(product_id=p.id)
               .order_by(PriceHistory.created_at.desc()).limit(10).all())
    verified, suggested = _market_state(p, p.shop)
    stats = get_market_stats(p.id, p.shop)
    pricing = get_price_recommendation(p.id, shop=p.shop)
    return render_template('product_detail.html', product=p, inventory=inv,
                           history=history, verified=verified,
                           suggested=suggested, stats=stats,
                           pricing=pricing,
                           can_edit=current_user.can('owner', 'manager'))


def _get_own_match(mid):
    """Fetch a match row + its product, enforcing shop isolation."""
    m = ProductMarketMatch.query.get_or_404(mid)
    if m.shop_product.shop_id != current_user.shop_id:
        abort(403)
    return m, m.shop_product


@app.route('/api/product/<int:pid>/market', methods=['GET'])
@login_required
def api_product_market(pid):
    """Current match state for the Market Intelligence tab (all roles)."""
    p = Product.query.get_or_404(pid)
    if p.shop_id != current_user.shop_id:
        abort(403)
    return _market_json(p)


@app.route('/api/product/<int:pid>/match', methods=['POST'])
@login_required
@role_required('owner', 'manager')
def api_product_match(pid):
    """(Re)run matching for one product and return the refreshed state."""
    p = Product.query.get_or_404(pid)
    if p.shop_id != current_user.shop_id:
        abort(403)
    created = apply_suggestions(p)
    db.session.commit()
    db.session.expire(p, ['market_matches'])   # reload the refreshed rows
    return _market_json(p)


@app.route('/api/market-match/<int:mid>/verify', methods=['POST'])
@login_required
@role_required('owner', 'manager')
def api_match_verify(mid):
    """Confirm a suggestion: is_verified=True, match_type='manual'."""
    m, product = _get_own_match(mid)
    if m.is_rejected:
        return jsonify({'error': 'Cannot verify a rejected suggestion.'}), 400
    m.is_verified = True
    m.match_type = 'manual'       # confirmed by a human = manual link
    db.session.commit()
    return _market_json(product)


@app.route('/api/market-match/<int:mid>/reject', methods=['POST'])
@login_required
@role_required('owner', 'manager')
def api_match_reject(mid):
    """Permanently hide a suggestion (is_rejected=True, never reappears)."""
    m, product = _get_own_match(mid)
    if m.is_verified:
        return jsonify({'error': 'Verified links are removed, not rejected.'}), 400
    m.is_rejected = True
    db.session.commit()
    return _market_json(product)


@app.route('/api/market-match/<int:mid>', methods=['DELETE'])
@login_required
@role_required('owner', 'manager')
def api_match_remove(mid):
    """Remove a verified link (the row itself is deleted)."""
    m, product = _get_own_match(mid)
    db.session.delete(m)
    db.session.commit()
    return _market_json(product)


# -------------------------------------------------
# PHASE 3E — PRICING RECOMMENDATION API
# -------------------------------------------------
@app.route('/api/product/<int:pid>/pricing', methods=['GET'])
@login_required
def api_pricing(pid):
    """ML-powered price recommendation with guardrails."""
    p = Product.query.get_or_404(pid)
    if p.shop_id != current_user.shop_id:
        abort(403)
    return jsonify(get_price_recommendation(pid))


@app.route('/api/product/<int:pid>/apply-price', methods=['POST'])
@login_required
@role_required('owner', 'manager')
def api_apply_price(pid):
    """Apply the recommended price to the product's selling_price."""
    p = Product.query.get_or_404(pid)
    if p.shop_id != current_user.shop_id:
        abort(403)
    new_price, msg = _apply_price(pid, current_user.id)
    return jsonify({'new_price': new_price, 'message': msg})


# -------------------------------------------------
# SALES
# -------------------------------------------------
@app.route('/sales')
@login_required
def sales():
    """Sales history for the current user's shop - newest first."""
    sales_list = (Sale.query.filter_by(shop_id=current_user.shop_id)
                  .order_by(Sale.sold_at.desc())
                  .all())
    #   - DATA ISOLATION: only this shop's sales are ever shown.
    return render_template('sales.html', sales=sales_list)


@app.route('/sales/new', methods=['GET', 'POST'])
@login_required
def new_sale():
    """Record ONE completed sale (per-unit price snapshot).
    Operational activity - all roles (owner/manager/staff) may record sales.
    The sale + inventory decrease happen in ONE database transaction: if
    either fails, neither is written."""
    products = (Product.query.filter_by(shop_id=current_user.shop_id)
                .order_by(Product.name).all())
    form = SaleForm()
    form.product_id.choices = [(p.id, p.name) for p in products]
    # Selling-price defaults (per product) for the frontend prefill.
    prices = {p.id: (str(p.selling_price) if p.selling_price is not None else '') 
              for p in products}

    if form.validate_on_submit():
        p = Product.query.get(form.product_id.data)
        if p is None or p.shop_id != current_user.shop_id:
            # Never trust a submitted product_id - it must be THIS shop's product.
            abort(403)

        qty = Decimal(str(form.quantity.data))
        price = Decimal(str(form.selling_price.data))

        inv = Inventory.query.filter_by(product_id=p.id).first()
        stock = inv.current_stock if inv else Decimal('0')
        if stock < qty:
            # Insufficient stock - reject BEFORE writing anything. No negative
            # inventory, ever.
            flash(f'Insufficient stock: only {stock} available, sale needs {qty}. '
                  f'Record a stock-in adjustment first.', 'danger')
            return render_template('sales_form.html', form=form,
                                   products=products, prices=prices)

        # ONE transaction: create the sale AND reduce stock together.
        try:
            db.session.add(Sale(shop_id=current_user.shop_id, product_id=p.id,
                                quantity=qty, selling_price=price))
            if inv is None:
                inv = Inventory(shop_id=current_user.shop_id, product_id=p.id,
                                current_stock=0, minimum_stock=0)
                db.session.add(inv)
            inv.current_stock = stock - qty
            db.session.commit()
            flash('Sale recorded!', 'success')
            return redirect(url_for('sales'))
        except Exception:
            # Any failure rolls back BOTH the sale and the stock change -
            # no partial records.
            db.session.rollback()
            flash('Failed to record the sale - no changes were saved.', 'danger')

    return render_template('sales_form.html', form=form, products=products,
                           prices=prices)


# -------------------------------------------------
# INVENTORY
# -------------------------------------------------
@app.route('/inventory')
@login_required
def inventory():
    """Stock levels for the current user's shop's products."""
    products = (Product.query.filter_by(shop_id=current_user.shop_id)
                .order_by(Product.name).all())
    inv_map = {i.product_id: i for i in
               Inventory.query.filter_by(shop_id=current_user.shop_id).all()}
    #   - DATA ISOLATION: only this shop's stock is ever shown.
    #   - Recent manual stock movements (owner/manager audit trail).
    movements = (InventoryAdjustment.query
                 .filter_by(shop_id=current_user.shop_id)
                 .order_by(InventoryAdjustment.created_at.desc())
                 .limit(10).all())
    return render_template('inventory.html', products=products,
                           inv_map=inv_map, movements=movements)


@app.route('/inventory/<int:pid>/adjust', methods=['GET', 'POST'])
@login_required
@role_required('owner', 'manager')  # staff may VIEW stock but not change it
#   - Inventory levels are shop-level data; adjusting them is owner/manager
#     territory (staff already cannot edit products).
def adjust_inventory(pid):
    """Adjust one product's stock by a +/- quantity with a reason.
    The resulting stock must never go negative."""
    p = Product.query.get_or_404(pid)
    if p.shop_id != current_user.shop_id:
        # SHOP OWNERSHIP CHECK: never trust the URL product id alone.
        abort(403)

    inv = Inventory.query.filter_by(product_id=p.id).first()
    form = InventoryAdjustmentForm()

    if form.validate_on_submit():
        change = Decimal(str(form.quantity_change.data))
        current = inv.current_stock if inv else Decimal('0')
        new_stock = current + change
        if new_stock < 0:
            # Never allow negative stock.
            flash(f'Cannot adjust below zero: current stock is {current}, '
                  f'removing {abs(change)} would make it {new_stock}.', 'danger')
            return render_template('inventory_adjust.html', form=form,
                                   product=p, inv=inv)

        # ONE transaction: apply the stock change + log the adjustment.
        if inv is None:
            inv = Inventory(shop_id=current_user.shop_id, product_id=p.id,
                            current_stock=0, minimum_stock=0)
            db.session.add(inv)
        inv.current_stock = new_stock
        db.session.add(InventoryAdjustment(
            shop_id=current_user.shop_id, product_id=p.id,
            quantity_change=change, reason=form.reason.data,
            user_id=current_user.id))
        db.session.commit()
        flash('Inventory adjusted.', 'success')
        return redirect(url_for('inventory'))

    return render_template('inventory_adjust.html', form=form, product=p, inv=inv)


@app.route('/inventory/<int:pid>/receive', methods=['GET', 'POST'])
@login_required
@role_required('owner', 'manager')  # staff may VIEW stock but not receive it
#   - Same rule as adjustments: stock is shop-level data, changed only by
#     owner/manager. Staff record sales (which reduce stock) but never add.
def receive_inventory(pid):
    """RECEIVE STOCK: the obvious 'how do I add stock?' workflow.
    Adds sellable units to a product's inventory. quantity_received must be
    > 0 (server-validated); the increase + the InventoryAdjustment audit row
    happen in ONE transaction - if either fails, both are rolled back."""
    p = Product.query.get_or_404(pid)
    if p.shop_id != current_user.shop_id:
        # SHOP OWNERSHIP CHECK - never trust the URL product id alone.
        abort(403)

    inv = Inventory.query.filter_by(product_id=p.id).first()
    form = ReceiveStockForm()

    if form.validate_on_submit():
        qty = Decimal(str(form.quantity_received.data))
        current = inv.current_stock if inv else Decimal('0')
        # qty > 0 is guaranteed by ReceiveStockForm (InputRequired + NumberRange).
        try:
            # ONE transaction: bump stock + log the stock-in adjustment.
            if inv is None:
                inv = Inventory(shop_id=current_user.shop_id, product_id=p.id,
                                current_stock=0, minimum_stock=0)
                db.session.add(inv)
            inv.current_stock = current + qty
            db.session.add(InventoryAdjustment(
                shop_id=current_user.shop_id, product_id=p.id,
                quantity_change=qty, reason=form.reason.data,
                user_id=current_user.id))
            db.session.commit()
            flash(f'Received {qty} unit(s). New stock: '
                  f'{inv.current_stock}.', 'success')
            return redirect(url_for('inventory'))
        except Exception:
            # Any failure rolls back BOTH the stock change and the audit row.
            db.session.rollback()
            flash('Failed to receive stock - no changes were saved.', 'danger')

    return render_template('inventory_receive.html', form=form, product=p,
                           inv=inv)


# -------------------------------------------------
# EMPLOYEE INVITATIONS & SHOP MEMBERSHIP (Phase 2C)
# -------------------------------------------------
@app.route('/employees', methods=['GET', 'POST'])
@login_required
@role_required('owner')   # only the OWNER manages employees - never manager/staff
def employees():
    """Owner-only employee management page.

    This page serves three functions:
      1. Display the current shop team (all users with this shop_id)
      2. Display invitation history (all ShopInvitation rows for this shop)
      3. Accept POST requests to create new invitations

    Security guarantees:
      - Only owners can access this page (enforced by role_required)
      - All queries are scoped to current_user.shop_id (shop isolation)
      - The invitation's shop_id comes from the owner's session, never the form
      - The invitation's role comes from the form dropdown, never the invitee
    """
    """Owner-only employee management page: current team, invitations and the
    invite form. Everything is scoped to current_user.shop_id - a manager of
    another shop can never see or touch this shop's team/invitations."""
    # Lazily expire pending invitations that have passed their 48h lifetime.
    for inv in ShopInvitation.query.filter_by(shop_id=current_user.shop_id,
                                              status='pending').all():
        if inv.is_expired:
            inv.status = 'expired'
    db.session.commit()

    team = (User.query.filter_by(shop_id=current_user.shop_id)
            .order_by(User.email).all())
    invitations = (ShopInvitation.query.filter_by(shop_id=current_user.shop_id)
                   .order_by(ShopInvitation.created_at.desc()).all())

    form = InviteForm()
    if form.validate_on_submit():
        email = norm_email(form.email.data)
        role = form.role.data
        existing = User.query.filter(func.lower(User.email) == email).first()
        if existing and existing.shop_id == current_user.shop_id:
            flash(f'{email} is already a member of this shop.', 'warning')
        elif ShopInvitation.query.filter_by(shop_id=current_user.shop_id,
                                            email=email,
                                            status='pending').first():
            # Avoid multiple simultaneously active invitations for one shop+email.
            flash('There is already a pending invitation for this email.', 'warning')
        else:
            now = datetime.now(timezone.utc)
            inv = ShopInvitation(
                shop_id=current_user.shop_id,      # owner's shop - NEVER from the form
                invited_by_user_id=current_user.id,
                email=email,
                role=role,
                token=secrets.token_urlsafe(32),   # unpredictable, 43 chars
                status='pending',
                created_at=now,
                expires_at=now + timedelta(hours=INVITATION_TTL_HOURS)
            )
            db.session.add(inv)
            db.session.commit()
            # If the invitee already has an (unassigned) account, deliver the
            # invitation as an in-app notification right away.
            if existing is not None and existing.shop_id is None:
                sync_pending_invitations(existing)
                db.session.commit()
            link = url_for('accept_invitation', token=inv.token, _external=True)
            flash(f'Invitation created for {email} ({role}). '
                  f'Share this link with them: {link}', 'success')
            return redirect(url_for('employees'))
    return render_template('employees.html', form=form, team=team,
                           invitations=invitations)


@app.route('/invitations/<int:iid>/revoke', methods=['POST'])
@login_required
@role_required('owner')
def revoke_invitation(iid):
    """Owner revokes a pending invitation. The row is KEPT for audit history
    (status -> revoked) and the token stops working."""
    inv = ShopInvitation.query.get_or_404(iid)
    if inv.shop_id != current_user.shop_id:
        # SHOP OWNERSHIP CHECK - never trust the URL id alone.
        abort(403)
    if inv.status == 'pending':
        inv.status = 'revoked'
        db.session.commit()
        flash('Invitation revoked.', 'success')
    else:
        flash('Only pending invitations can be revoked.', 'warning')
    return redirect(url_for('employees'))


@app.route('/employees/<int:uid>/remove', methods=['POST'])
@login_required
@role_required('owner')
def remove_employee(uid):
    """Owner removes a manager/staff member from the shop.

    The employee's ACCOUNT is KEPT - it is only unassigned (shop_id -> NULL,
    role -> 'unassigned'), the same state a fresh "Join an existing shop"
    account starts in. The row is never deleted because the account is needed
    for login / notifications / re-invitation, and rows like
    inventory_adjustment.user_id reference it. After removal the owner can
    invite the same email again (the invite-time guard only blocks current
    members of the shop).

    Safety rules: the owner cannot remove themselves, cannot remove another
    owner, and can only touch users of their OWN shop."""
    target = User.query.get_or_404(uid)
    if target.shop_id != current_user.shop_id:
        # SHOP OWNERSHIP CHECK - never trust the URL id alone.
        abort(403)
    if target.id == current_user.id:
        flash('You cannot remove yourself — you are the shop owner.', 'warning')
        return redirect(url_for('employees'))
    if target.role == 'owner':
        flash('Shop owners cannot be removed by another user.', 'warning')
        return redirect(url_for('employees'))

    shop_name = current_user.shop.name if current_user.shop else 'the shop'
    target.shop_id = None
    target.role = 'unassigned'
    # Tell the employee (their notifications page renders non-invitation
    # types as plain messages).
    db.session.add(Notification(
        user_id=target.id,
        type='shop_membership',
        title='Removed from shop',
        message=f'You have been removed from {shop_name} by the shop owner.',
    ))
    db.session.commit()
    flash(f'{target.email} has been removed from the shop.', 'success')
    return redirect(url_for('employees'))


@app.route('/notifications')
@login_required
def notifications():
    """The in-app notification inbox. Surfacing any pending invitation that
    arrived for this user, then marking everything read (unread = the 🔔
    badge count shown before the page is opened)."""
    sync_pending_invitations(current_user)
    items = (Notification.query.filter_by(user_id=current_user.id)
             .order_by(Notification.created_at.desc()).all())
    # Keep track of which were still unread when the page was opened so the
    # template can show a "NEW" badge before we flip them to read.
    new_ids = {n.id for n in items if not n.is_read}
    for n in items:
        n.is_read = True
    db.session.commit()
    return render_template('notifications.html', notifications=items,
                           new_ids=new_ids)


@app.route('/invitations/<int:iid>/accept', methods=['POST'])
@login_required
def accept_invitation_id(iid):
    """Employee accepts an invitation (from a notification or the accept page).
    The invitation row is AUTHORITATIVE for shop_id + role - nothing is read
    from the submitted form. The accept only succeeds for the exact account
    the invitation was addressed to, and only when the invitation is pending,
    unexpired and the user has no conflicting shop membership."""
    inv = ShopInvitation.query.get_or_404(iid)
    # 1. The invitation belongs to THIS user's email - never someone else's.
    if norm_email(inv.email) != norm_email(current_user.email):
        abort(403)
    # 2. Lazily expire past-48h invitations.
    if inv.status == 'pending' and inv.is_expired:
        inv.status = 'expired'
        db.session.commit()
    # 3. Only a pending invitation can be accepted (no reuse).
    if inv.status != 'pending':
        flash('This invitation is no longer valid.', 'danger')
        return redirect(url_for('notifications'))
    # 4. Conflicting membership? Never move an existing user (incl. owners).
    if current_user.shop_id is not None and current_user.shop_id != inv.shop_id:
        flash('This account already belongs to another shop. The invitation '
              'cannot be accepted and the account was not moved.', 'danger')
        return redirect(url_for('dashboard'))
    # 5. Already a member of the invited shop? Nothing to change.
    if current_user.shop_id == inv.shop_id:
        inv.status = 'accepted'
        mark_invitation_notifications_read(inv)
        db.session.commit()
        flash('This account is already a member of the shop. '
              'Invitation marked as accepted.', 'success')
        return redirect(url_for('dashboard'))
    # 6. Unassigned employee -> join the shop with the invitation's role.
    current_user.shop_id = inv.shop_id
    current_user.role = inv.role
    inv.status = 'accepted'
    mark_invitation_notifications_read(inv)
    db.session.commit()
    flash(f'Welcome to {inv.shop.name} as {inv.role.capitalize()}!', 'success')
    return redirect(url_for('dashboard'))


@app.route('/invitations/<int:iid>/reject', methods=['POST'])
@login_required
def reject_invitation(iid):
    """Employee DECLINES a pending invitation (status -> rejected, distinct
    from owner-revoked). The invitation stays in the database for audit."""
    inv = ShopInvitation.query.get_or_404(iid)
    # Only the invited account may reject its own invitation.
    if norm_email(inv.email) != norm_email(current_user.email):
        abort(403)
    if inv.status == 'pending' and inv.is_expired:
        inv.status = 'expired'
        db.session.commit()
    if inv.status != 'pending':
        flash('This invitation is no longer valid.', 'warning')
        return redirect(url_for('notifications'))
    inv.status = 'rejected'
    mark_invitation_notifications_read(inv)
    db.session.commit()
    flash('Invitation declined.', 'success')
    return redirect(url_for('notifications'))


@app.route('/invite/accept/<token>', methods=['GET', 'POST'])
def accept_invitation(token):
    """The shareable invitation link endpoint.

    This is the public-facing URL that the owner shares with the invitee.
    The token is a cryptographically secure random string — it is the
    credential that grants access to the invitation.

    Flow:
      1. Validate the token exists and the invitation is still pending.
      2. Lazily expire past-48h invitations.
      3. Check if the invited email already has an account:
         a. If YES and logged in as that account: show Accept/Reject.
         b. If YES but not logged in as that account: redirect to login.
         c. If NO: show the registration form to create an employee account.
      4. On account creation: sync_pending_invitations surfaces the invite.

    Security guarantees:
      - shop_id and role come ONLY from the invitation row (authoritative)
      - The invitee cannot tamper with these values through form fields
      - Existing users of OTHER shops are never moved (blocked)
      - Owners of other shops are never silently demoted
    """
    """The shareable invitation link. The shop_id + role come ONLY from the
    invitation row - never from the request.
      A) invited email has NO account -> create an employee account (no shop
         membership) and send the user to /notifications to EXPLICITLY accept.
      B) invited email has an account -> must log in as that account; then the
         page shows Accept/Reject buttons (or a clear message for same/different
         shop cases).
    An existing account belonging to ANOTHER shop is rejected - it is never
    silently moved."""
    inv = ShopInvitation.query.filter_by(token=token).first()
    if inv is None:
        flash('Invalid invitation link.', 'danger')
        return redirect(url_for('login'))

    # Lazily expire pending invitations past their 48h lifetime.
    if inv.status == 'pending' and inv.is_expired:
        inv.status = 'expired'
        db.session.commit()

    if inv.status != 'pending':
        # accepted / rejected / revoked / expired - the token cannot be reused.
        return render_template('invite_status.html', invitation=inv)

    email = norm_email(inv.email)
    existing = User.query.filter(func.lower(User.email) == email).first()

    if existing is not None:
        # ---- This email already has an account ----
        if not (current_user.is_authenticated and current_user.id == existing.id):
            # Must authenticate AS the invited account - never move a
            # stranger's account. Send them to login and back to this link.
            flash('This email already has an account. Log in as that account '
                  'to accept the invitation.', 'info')
            return redirect(url_for('login', next=request.path))

        if existing.shop_id is not None and existing.shop_id != inv.shop_id:
            # Belongs to ANOTHER shop (including an owner who owns their own
            # shop). Reject - never silently move an existing user.
            flash('This account already belongs to another shop. The invitation '
                  'cannot be accepted and the account was not moved.', 'danger')
            return redirect(url_for('dashboard'))

        if existing.shop_id == inv.shop_id:
            # Already a member - nothing to change, no duplicate user.
            inv.status = 'accepted'
            mark_invitation_notifications_read(inv)
            db.session.commit()
            flash('This account is already a member of the shop. '
                  'Invitation marked as accepted.', 'success')
            return redirect(url_for('dashboard'))

        # Unassigned matching account -> explicit Accept/Reject on this page.
        return render_template('invite_accept.html', form=None, invitation=inv,
                               mode='summary')

    # ---- No account yet - create an EMPLOYEE account (Path B) -----------
    # The account is created WITHOUT shop membership; the invitation stays
    # pending and the employee must explicitly ACCEPT it from /notifications.
    form = InviteAcceptForm()
    if form.validate_on_submit():
        u = User(email=email, role='unassigned', shop_id=None)
        u.set_password(form.password.data)
        db.session.add(u)
        db.session.flush()               # obtain u.id for the notification
        sync_pending_invitations(u)      # surfaces THIS invitation as a notification
        db.session.commit()
        login_user(u)
        flash(f'Account created! Accept your invitation to join '
              f'{inv.shop.name} from your notifications.', 'success')
        return redirect(url_for('notifications'))
    return render_template('invite_accept.html', form=form, invitation=inv,
                           mode='register')


# ------------------------- ERROR HANDLERS -------------------------
# Friendly pages instead of raw stack traces for the user.

@app.errorhandler(404)
def not_found_error(error):
    return render_template('errors/404.html'), 404


@app.errorhandler(403)
def forbidden_error(error):
    return render_template('errors/403.html'), 403


@app.errorhandler(500)
def internal_error(error):
    # Log the full traceback for debugging without exposing it to the user
    app.logger.error(f"Server Error: {error}", exc_info=True)
    db.session.rollback()  # clear any broken/half-committed DB transaction
    #   - Important: if a request died mid-transaction, MySQL would hold a
    #     broken session. Rollback keeps the next request clean.
    return render_template('errors/500.html'), 500


@app.errorhandler(CSRFError)
def csrf_error(error):
    # Expired/invalid CSRF token (e.g. page left open too long).
    flash('Your session security token expired or was invalid. Please try again.', 'danger')
    return redirect(request.referrer or url_for('login'))


# ------------------------- SCHEMA MANAGEMENT -------------------------
# Schema is managed by Flask-Migrate (migrations/), NOT by db.create_all().
# This keeps the database under version control and prevents drift between
# the models and the live tables. Apply changes with:
#     flask db migrate -m "description"   # generate a new migration
#     flask db upgrade                    # apply pending migrations

if __name__ == '__main__':
    app.run(debug=True)     # dev server - turn debug off in production
