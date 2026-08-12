"""
============================================================
 ShelfSense AI — Form definitions (forms.py)
============================================================
 This file defines every input form the app accepts, using WTForms.

 WHY forms matter for the backend:
   - SERVER-SIDE VALIDATION: every rule below is enforced in Python on the
     server, so a user cannot bypass them by editing the HTML/JS.
   - CSRF SECURITY: every FlaskForm automatically embeds a hidden CSRF token.
     On submit, Flask-WTF rejects the request if the token is missing/expired.
   - SAFE DATA: validated form data is what we trust to write into MySQL.

 The routes in app.py use these classes like this:
     form = LoginForm()                 # build the form
     form.validate_on_submit()          # POST + all validators pass?
     form.email.data                    # the validated value
============================================================
"""

from flask_wtf import FlaskForm
#   - FlaskForm = base class. It adds the CSRF token + hidden_tag()
#     support that plain WTForms doesn't have.

from wtforms import (StringField, PasswordField, FloatField, DecimalField,
                     BooleanField, SelectField, SubmitField)
#   - Each *Field = one type of input:
#       StringField   → <input type="text">
#       PasswordField → <input type="password"> (masked on screen)
#       FloatField    → decimal number input (RM prices, percentages)
#       DecimalField  → exact-decimal input (money - no float noise)
#       BooleanField  → checkbox ("Remember Me")
#       SubmitField   → the submit button

from wtforms.validators import (DataRequired, Email, EqualTo, InputRequired,
                                Length, NumberRange, Optional, ValidationError)
#   - Validators = the rules. WTForms runs them on submit and fails the
#     form if any rule is violated:
#       DataRequired   → field must not be empty
#       Email          → must look like a real email address
#       EqualTo        → must equal another field (password confirmation)
#       Length         → min/max character count (passwords, names)
#       NumberRange    → numeric min/max (cost, margin)
#       Optional       → empty field is OK (skips the other validators)
#       ValidationError → raised manually for custom cross-field checks
#   - Validators = the rules. WTForms runs them on submit and fails the
#     form if any rule is violated:
#       DataRequired → field must not be empty
#       Email        → must look like a real email address
#       Length       → min/max character count (passwords, names)
#       NumberRange  → numeric min/max (cost, margin)


class LoginForm(FlaskForm):
    """Login screen fields. No Email validator here on purpose:
    an attacker should not learn which emails are registered — any input
    gets the same generic "Invalid credentials" message."""
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Remember Me')
    #   - remember=True → Flask-Login writes a persistent cookie so the
    #     user stays logged in across browser restarts.
    submit = SubmitField('Log In')


class RegisterForm(FlaskForm):
    """New-account fields.
    NEW SHOP-BASED FLOW: there is NO public role dropdown any more. Every
    registration creates a brand-new shop, and the registrant automatically
    becomes its OWNER (role='owner'). Future invitation flow (owner invites
    manager/staff into the same shop) will add users to an existing shop_id."""
    shop_name = StringField('Shop Name', validators=[DataRequired(), Length(max=120)])
    #   - The new shop's display name (e.g. "Demo Retail Shop").
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    #   - Length(min=6): enforce a minimum password length on the SERVER,
    #     not just in the browser.
    confirm = PasswordField('Confirm Password',
                            validators=[DataRequired(),
                                        EqualTo('password',
                                                message='Passwords must match')])
    #   - EqualTo: server-side password/confirm check. No longer relies on
    #     client-side JS alone - the server rejects mismatched passwords.
    submit = SubmitField('Register')


class ProductForm(FlaskForm):
    """Add/Edit product form — used by BOTH /product/new and /product/<id>/edit.
    Fields build the product's identity (name/brand/category/quantity/unit) plus
    its economics (cost, current selling price, target margin)."""
    name = StringField('Product Name', validators=[DataRequired(), Length(max=120)])
    #   - Required; the shop's own name. It does NOT need to exist in PriceCatcher.
    brand = StringField('Brand (optional)', validators=[Length(max=80)])
    #   - Optional (e.g. MILO, MAGGI) - not every product has a clear brand.
    category = StringField('Category (optional)', validators=[Length(max=80)])
    #   - Optional — the autocomplete API fills this from PriceCatcher data.
    quantity = FloatField('Quantity (optional)', validators=[Optional(), NumberRange(min=0.01)])
    #   - Optional numeric amount (e.g. 1, 500, 1000). Must be > 0 when supplied;
    #     interpreted together with `unit` (1 + kg, NOT the string "1kg").
    unit = StringField('Unit (optional)', validators=[Length(max=20)])
    #   - Optional unit of measure: kg, g, L, ml, pcs, pack, box...
    cost_price = FloatField('Cost Price (RM)', validators=[DataRequired(), NumberRange(min=0.01)])
    #   - NumberRange(min=0.01): a product must cost at least RM0.01 —
    #     prevents zero/negative prices entering the pricing engine.
    selling_price = DecimalField('Selling Price (RM)', validators=[Optional(), NumberRange(min=0.01)])
    #   - Optional CURRENT price the shop charges customers (RM). Distinct from the
    #     suggested price (cost x margin). DecimalField = exact money, no float noise.
    target_margin = FloatField('Target Margin %', validators=[DataRequired(), NumberRange(min=0, max=1000)])
    #   - Margin bounded 0–1000% to keep the suggested-price formula sane.
    submit = SubmitField('Save')

    def validate_unit(self, field):
        # A quantity only makes sense with a unit (1 kg vs 1 pcs) - if the user
        # supplied a number, require the unit too.
        if self.quantity.data and not field.data:
            raise ValidationError('Unit is required when Quantity is provided.')


class SaleForm(FlaskForm):
    """Record a completed sale. The product list is filled by the route with
    ONLY the current user's shop products (isolation). selling_price is the
    PER-UNIT price actually charged at the time of the sale - a snapshot,
    independent of the product's current price."""
    product_id = SelectField('Product', coerce=int, validators=[DataRequired()],
                             validate_choice=False)
    #   - choices set per-request in the route: [(p.id, p.name), ...]
    #   - validate_choice=False: the SELECTION is just a convenience dropdown.
    #     Authorization is enforced in the route (product must belong to the
    #     current user's shop, else 403) - never trust the submitted id alone.
    quantity = FloatField('Quantity Sold',
                          validators=[InputRequired(), NumberRange(min=0.01)])
    #   - must be > 0: zero/negative sales are rejected (no freebies in this phase).
    selling_price = DecimalField('Selling Price (RM)',
                                 validators=[InputRequired(), NumberRange(min=0.01)])
    #   - per-unit price AT SALE TIME, >= RM0.01. DecimalField = exact money.
    submit = SubmitField('Record Sale')


class InviteForm(FlaskForm):
    """Owner-only invite form. Only manager/staff may be invited - an owner
    can never invite another owner (and the role is fixed at invite time;
    the invitee cannot change it during acceptance)."""
    email = StringField('Email', validators=[DataRequired(), Email()])
    role = SelectField('Role',
                       choices=[('manager', 'Manager'), ('staff', 'Staff')],
                       validators=[DataRequired()])
    submit = SubmitField('Send Invitation')


class InviteAcceptForm(FlaskForm):
    """Account-creation form shown when an invited email has NO account yet.
    Email / shop / role are NOT fields here - they come from the invitation
    row itself, so the invitee cannot tamper with them (tests J & K)."""
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm = PasswordField('Confirm Password',
                            validators=[DataRequired(),
                                        EqualTo('password',
                                                message='Passwords must match')])
    submit = SubmitField('Accept Invitation')


class InventoryAdjustmentForm(FlaskForm):
    """Manual stock adjustment for ONE product (the product is pinned by the
    route URL, so there is no product selector here - the URL is the identity
    and it is shop-verified server-side)."""
    quantity_change = FloatField('Adjustment (+/-)',
                                 validators=[InputRequired()])
    #   - positive = stock in, negative = stock out. InputRequired (not
    #     DataRequired) so 0 reaches the custom validator with a clear message.
    reason = StringField('Reason', validators=[DataRequired(), Length(max=200)])
    #   - required so every stock movement stays traceable (e.g. "Stock received").
    submit = SubmitField('Apply Adjustment')

    def validate_quantity_change(self, field):
        # A zero adjustment is meaningless and would pollute the audit trail.
        if field.data is not None and field.data == 0:
            raise ValidationError('Adjustment cannot be zero.')
