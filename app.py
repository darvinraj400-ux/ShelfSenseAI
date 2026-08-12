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
                   InventoryAdjustmentForm, InviteForm, InviteAcceptForm)
#   - Our own WTForms definitions (email format, min password length, etc.)

from flask_migrate import Migrate          # DB schema migration tool (like git for tables)
from flask_wtf import CSRFProtect          # cross-site request forgery protection
from flask_wtf.csrf import CSRFError       # the exception raised when a CSRF token is bad
from dotenv import load_dotenv             # reads our .env config file
from datetime import datetime, timezone, timedelta  # timestamps + invitation expiry
from functools import wraps                # used by our role_required() decorator
from decimal import Decimal                # exact money/quantity arithmetic (no float noise)
import secrets                            # cryptographically secure invitation tokens
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
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    #   - users / products relationships come from backrefs on User and Product.


class User(UserMixin, db.Model):
    """Registered users with a role: owner / manager / staff.
    Every user belongs to exactly one Shop (shop_id)."""
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)  # never the raw password!
    role = db.Column(db.String(20), nullable=False, default='staff')  # owner | manager | staff
    shop_id = db.Column(db.Integer, db.ForeignKey('shop.id'), nullable=False)
    #   - FK to shop.id. Public registration always creates a NEW shop and makes
    #     the registrant its owner, so shop_id is always set. Future invitation
    #     flow: an owner adds manager/staff to their EXISTING shop_id.
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
    """One row per MANUAL stock adjustment. Sale-driven stock decreases are
    NOT logged here - the sale row itself is the trace for those."""
    __tablename__ = 'inventory_adjustment'
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey('shop.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity_change = db.Column(db.Numeric(10, 3), nullable=False)  # +20 / -5
    reason = db.Column(db.String(200), nullable=False)              # why the change happened
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    shop = db.relationship('Shop', backref='inventory_adjustments')
    product = db.relationship('Product',
                              backref=db.backref('inventory_adjustments',
                                                 cascade='all, delete-orphan'))
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
        if User.query.filter_by(email=form.email.data).first():
            flash('Email already registered', 'danger')
        else:
            # NEW SHOP-BASED FLOW: public registration always creates a brand
            # new shop and the registrant becomes its OWNER. There is no public
            # role dropdown - a stranger cannot register as the owner/manager
            # of an existing shop (future invitations handle joining a shop).
            shop = Shop(name=form.shop_name.data)
            db.session.add(shop)
            db.session.flush()               # obtain shop.id for the new user
            u = User(email=form.email.data, role='owner', shop_id=shop.id)
            u.set_password(form.password.data)   # hash the password before saving
            db.session.add(u)                    # queue the insert
            db.session.commit()                  # write shop + user to MySQL
            flash('Registered! Please log in.', 'success')
            return redirect(url_for('login'))
    return render_template('register.html', form=form)


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Verify email + password, then start a Flask-Login session."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    form = LoginForm()
    if form.validate_on_submit():
        u = User.query.filter_by(email=form.email.data).first()
        if u and u.check_password(form.password.data):
            # check_password hashes the typed password and compares to the stored hash.
            login_user(u, remember=form.remember.data)   # remember = persistent cookie
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


@app.route('/dashboard')
@login_required
def dashboard():
    # DATA ISOLATION: products are owned by the SHOP, not the user. Every
    # member of a shop (owner/manager/staff) sees the SAME products; users
    # of other shops never see them. This filter is the whole security model.
    products = Product.query.filter_by(shop_id=current_user.shop_id).all()
    # Map product_id -> inventory row so the table can show current stock.
    inv_map = {i.product_id: i for i in
               Inventory.query.filter_by(shop_id=current_user.shop_id).all()}
    return render_template('dashboard.html', products=products, inv_map=inv_map)


@app.route('/product/new', methods=['GET', 'POST'])
@login_required
@role_required('owner', 'manager')  # staff may NOT add products
def new_product():
    form = ProductForm()
    if form.validate_on_submit():
        # The shop_id always comes from the logged-in user - NEVER from the
        # submitted form (an attacker cannot re-point a product at another shop).
        p = Product(
            name=form.name.data,
            brand=(form.brand.data or '').strip() or None,
            category=(form.category.data or '').strip() or None,
            quantity=form.quantity.data,
            unit=(form.unit.data or '').strip() or None,
            cost_price=form.cost_price.data,
            selling_price=form.selling_price.data,
            target_margin=form.target_margin.data,
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
        # Normalize optional text fields: blank input -> NULL (not empty string).
        p.brand = (p.brand or '').strip() or None
        p.unit = (p.unit or '').strip() or None
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
    return render_template('inventory.html', products=products, inv_map=inv_map)


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
            quantity_change=change, reason=form.reason.data))
        db.session.commit()
        flash('Inventory adjusted.', 'success')
        return redirect(url_for('inventory'))

    return render_template('inventory_adjust.html', form=form, product=p, inv=inv)


# -------------------------------------------------
# EMPLOYEE INVITATIONS & SHOP MEMBERSHIP (Phase 2C)
# -------------------------------------------------
@app.route('/employees', methods=['GET', 'POST'])
@login_required
@role_required('owner')   # only the OWNER manages employees - never manager/staff
def employees():
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
        email = form.email.data.strip().lower()
        role = form.role.data
        existing = User.query.filter_by(email=email).first()
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


@app.route('/invite/accept/<token>', methods=['GET', 'POST'])
def accept_invitation(token):
    """Accept an invitation. The shop_id + role come ONLY from the invitation
    row - never from the request. Three cases:
      A) invited email has no account  -> registration form (email/role fixed)
      B) invited email has an account  -> must log in as that account first
      C) account already in this shop  -> accepted, nothing moved
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
        # accepted / revoked / expired - the token cannot be reused.
        return render_template('invite_status.html', invitation=inv)

    existing = User.query.filter_by(email=inv.email).first()

    if existing is not None:
        # ---- Cases B & C: this email already has an account ----
        if not (current_user.is_authenticated and current_user.id == existing.id):
            # Must authenticate AS the invited account - never move a
            # stranger's account. Send them to login and back to this link.
            flash('This email already has an account. Log in as that account '
                  'to accept the invitation.', 'info')
            return redirect(url_for('login', next=request.path))

        if existing.shop_id == inv.shop_id:
            # Case C: already a member - nothing to change, no duplicate user.
            inv.status = 'accepted'
            db.session.commit()
            flash('This account is already a member of the shop. '
                  'Invitation marked as accepted.', 'success')
            return redirect(url_for('dashboard'))

        # Case B: the account belongs to ANOTHER shop (including an owner who
        # owns their own shop). Reject - never silently move an existing user.
        flash('This account already belongs to another shop. The invitation '
              'cannot be accepted and the account was not moved.', 'danger')
        return redirect(url_for('dashboard'))

    # ---- Case A: invited email has no account yet - create it here -------
    form = InviteAcceptForm()
    if form.validate_on_submit():
        u = User(email=inv.email, role=inv.role, shop_id=inv.shop_id)
        #   - role + shop come from the invitation row, not the request.
        u.set_password(form.password.data)
        inv.status = 'accepted'
        try:
            db.session.add(u)
            db.session.commit()   # single transaction: user + status together
        except Exception:
            # Any failure rolls back; the invitation stays usable (pending).
            db.session.rollback()
            flash('Could not complete the invitation - please try again.', 'danger')
            return render_template('invite_accept.html', form=form,
                                   invitation=inv)
        login_user(u)             # straight into the new shop
        flash(f'Welcome! You joined {inv.shop.name} as {inv.role}.', 'success')
        return redirect(url_for('dashboard'))
    return render_template('invite_accept.html', form=form, invitation=inv)


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
