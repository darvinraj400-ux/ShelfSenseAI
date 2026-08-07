from flask import Flask, render_template, redirect, url_for, flash, request, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from forms import LoginForm, RegisterForm, ProductForm
from flask_migrate import Migrate
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from dotenv import load_dotenv
from datetime import datetime, timezone
from functools import wraps
import os
import pymysql

load_dotenv()  # loads .env file
db_url = os.getenv("DATABASE_URL")
if not db_url:
    raise RuntimeError("DATABASE_URL not found in .env file")

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret-change-in-prod'
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

import pymysql
pymysql.install_as_MySQLdb()

db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- CSRF Protection ---
# FlaskForm already validates a CSRF token on any form using hidden_tag(),
# but CSRFProtect(app) extends that same protection to EVERY POST/PUT/PATCH/DELETE
# route app-wide, including plain HTML forms (like the delete button) that
# don't use a WTForm. This is the standard Flask-WTF approach.
csrf = CSRFProtect(app)

# --- Models ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='staff')  # owner | manager | staff
    products = db.relationship('Product', backref='owner', lazy=True)

    def can(self, *roles):
        """Convenience check: is this user allowed one of the given roles?
        Full permission matrix lands at the 80% milestone."""
        return self.role in roles

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    cost_price = db.Column(db.Float, nullable=False)
    target_margin = db.Column(db.Float, nullable=False)  # percentage e.g. 30.0
    baseline_margin = db.Column(db.Float, nullable=True)  # margin locked at creation (PCAPA baseline)
    category = db.Column(db.String(80), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    history = db.relationship('PriceHistory', backref='product', lazy=True,
                              cascade='all, delete-orphan',
                              order_by='PriceHistory.created_at')

    @property
    def suggested_price(self):
        return round(self.cost_price * (1 + self.target_margin / 100), 2)


# -------------------------------------------------
# NEW MODEL – Price history log (cost/margin changes per product)
# Used for PCAPA-style margin-baseline tracking: every cost or margin
# change is recorded so an unexplained margin increase is visible.
# -------------------------------------------------
class PriceHistory(db.Model):
    __tablename__ = 'price_history'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    cost_price = db.Column(db.Float, nullable=False)
    target_margin = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
# -------------------------------------------------


# -------------------------------------------------
# NEW MODELS – PriceCatcher lookup tables
# -------------------------------------------------
class LookupItem(db.Model):
    __tablename__ = 'lookup_item'
    item_code   = db.Column(db.String(50), primary_key=True)
    item        = db.Column(db.String(255), nullable=False)   # product name
    unit        = db.Column(db.String(50))
    item_group  = db.Column(db.String(100))
    item_category = db.Column(db.String(100))   # ← used for autofill


class LookupPremise(db.Model):
    __tablename__ = 'lookup_premise'
    premise_code = db.Column(db.String(50), primary_key=True)
    premise      = db.Column(db.String(255), nullable=False)   # shop/outlet name
    address      = db.Column(db.String(255))
    premise_type = db.Column(db.String(50))
    state        = db.Column(db.String(50))
    district     = db.Column(db.String(50))


class Price(db.Model):
    __tablename__ = 'price'
    price_id    = db.Column(db.Integer, primary_key=True, autoincrement=True)
    date        = db.Column(db.Date, nullable=False)
    price       = db.Column(db.Numeric(10, 2), nullable=False)
    item_code   = db.Column(db.String(50), db.ForeignKey('lookup_item.item_code'), nullable=False)
    premise_code= db.Column(db.String(50), db.ForeignKey('lookup_premise.premise_code'), nullable=False)
# -------------------------------------------------


# -------------------------------------------------
# NEW ROUTE – Autocomplete for PriceCatcher items
# -------------------------------------------------
@app.route('/autocomplete')
@login_required  # only logged‑in users may use the lookup
def autocomplete():
    """
    Return up to 10 items from lookup_item whose 'item' column starts with the
    supplied term (case‑insensitive).  Each result includes the fields needed
    to fill the product name and category inputs.
    """
    from sqlalchemy import func

    term = request.args.get('q', '').strip()
    if not term or len(term) < 2:
        # Too short – return empty list so the JS hides the dropdown
        return jsonify([])

    # Case‑insensitive prefix search
    matches = (LookupItem.query
                   .filter(func.lower(LookupItem.item).like(func.lower(term) + '%'))
                   .order_by(LookupItem.item)
                   .limit(10)
                   .all())

    # Build JSON payload
    results = []
    for m in matches:
        results.append({
            'item_code': m.item_code,
            'item': m.item,                     # → Product Name field
            'item_category': m.item_category,   # → Category field
            'unit': m.unit                      # optional, for future use
        })

    return jsonify(results)
# -------------------------------------------------


@login_manager.user_loader
def load_user(uid):
    return User.query.get(int(uid))


# -------------------------------------------------
# Role-based access control (stub for the 80% milestone)
# -------------------------------------------------
def role_required(*roles):
    """Decorator: restrict a view to users with one of the given roles.
    Stub — the full permission matrix (what each role may do) is defined
    at the 80% milestone. Usage:
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


# --- Routes ---
@app.route('/')
def home():
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    form = RegisterForm()
    if form.validate_on_submit():
        if User.query.filter_by(email=form.email.data).first():
            flash('Email already registered', 'danger')
        else:
            # NOTE (80% milestone): currently anyone can self-select 'owner'.
            # Before role_required() guards any sensitive route, restrict this
            # (e.g. first-registered-user-is-owner, or admin approves owners).
            u = User(email=form.email.data, role=form.role.data)
            u.set_password(form.password.data)
            db.session.add(u)
            db.session.commit()
            flash('Registered! Please log in.', 'success')
            return redirect(url_for('login'))
    return render_template('register.html', form=form)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    form = LoginForm()
    if form.validate_on_submit():
        u = User.query.filter_by(email=form.email.data).first()
        if u and u.check_password(form.password.data):
            login_user(u, remember=form.remember.data)
            return redirect(url_for('dashboard'))
        flash('Invalid credentials', 'danger')
    return render_template('login.html', form=form)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    products = Product.query.filter_by(user_id=current_user.id).all()
    return render_template('dashboard.html', products=products)


@app.route('/product/new', methods=['GET', 'POST'])
@login_required
def new_product():
    form = ProductForm()
    if form.validate_on_submit():
        p = Product(
            name=form.name.data,
            cost_price=form.cost_price.data,
            target_margin=form.target_margin.data,
            category=form.category.data,
            user_id=current_user.id
        )
        p.baseline_margin = p.target_margin  # lock the margin baseline at creation
        db.session.add(p)
        db.session.flush()  # obtain p.id for the history row
        db.session.add(PriceHistory(
            product_id=p.id,
            cost_price=p.cost_price,
            target_margin=p.target_margin
        ))
        db.session.commit()
        flash('Product added!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('product_form.html', form=form, title='Add Product',
                           baseline_margin=None, prev_cost=None, history=None)


@app.route('/product/<int:pid>/edit', methods=['GET', 'POST'])
@login_required
def edit_product(pid):
    p = Product.query.get_or_404(pid)
    if p.user_id != current_user.id:
        flash('Not authorized', 'danger')
        return redirect(url_for('dashboard'))
    form = ProductForm(obj=p)
    if form.validate_on_submit():
        old_cost = p.cost_price
        old_margin = p.target_margin
        new_cost = form.cost_price.data
        new_margin = form.target_margin.data

        # Products created before baseline tracking existed: adopt the current
        # margin as their baseline (there is no older data to reconstruct).
        if p.baseline_margin is None:
            p.baseline_margin = old_margin

        form.populate_obj(p)

        # Log every cost/margin change — the visible "cost history" trail.
        if new_cost != old_cost or new_margin != old_margin:
            db.session.add(PriceHistory(
                product_id=p.id,
                cost_price=new_cost,
                target_margin=new_margin
            ))

        # PCAPA-style compliance check: raising the margin beyond baseline with
        # no cost increase is an *unexplained* margin increase → profiteering risk.
        if new_margin > p.baseline_margin and new_cost <= old_cost:
            flash(
                f'⚠️ Compliance warning: margin raised from {p.baseline_margin:g}% to '
                f'{new_margin:g}% with no cost increase. Under the Price Control and '
                f'Anti-Profiteering Act 2011, only a cost increase justifies a higher '
                f'margin — an unexplained increase is treated as profiteering risk.',
                'warning'
            )

        db.session.commit()
        flash('Product updated!', 'success')
        return redirect(url_for('dashboard'))

    history = (PriceHistory.query.filter_by(product_id=p.id)
               .order_by(PriceHistory.created_at.desc()).limit(10).all())
    return render_template('product_form.html', form=form, title='Edit Product',
                           baseline_margin=p.baseline_margin, prev_cost=p.cost_price,
                           history=history)


@app.route('/product/<int:pid>/delete', methods=['POST'])
@login_required
def delete_product(pid):
    p = Product.query.get_or_404(pid)
    if p.user_id != current_user.id:
        flash('Not authorized', 'danger')
        return redirect(url_for('dashboard'))
    db.session.delete(p)
    db.session.commit()
    flash('Product deleted', 'success')
    return redirect(url_for('dashboard'))


# --- Error Handlers ---
@app.errorhandler(404)
def not_found_error(error):
    return render_template('errors/404.html'), 404


@app.errorhandler(403)
def forbidden_error(error):
    return render_template('errors/403.html'), 403


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()  # clear any broken/half-committed DB transaction
    return render_template('errors/500.html'), 500


@app.errorhandler(CSRFError)
def csrf_error(error):
    flash('Your session security token expired or was invalid. Please try again.', 'danger')
    return redirect(request.referrer or url_for('login'))


# --- Init DB ---
with app.app_context():
    db.create_all()


if __name__ == '__main__':
    app.run(debug=True)