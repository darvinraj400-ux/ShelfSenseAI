from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, FloatField, BooleanField, SubmitField
from wtforms.validators import DataRequired, Email, Length, NumberRange

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Remember Me')
    submit = SubmitField('Log In')

class RegisterForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm = PasswordField('Confirm Password', validators=[DataRequired()])
    submit = SubmitField('Register')

class ProductForm(FlaskForm):
    name = StringField('Product Name', validators=[DataRequired(), Length(max=120)])
    cost_price = FloatField('Cost Price (RM)', validators=[DataRequired(), NumberRange(min=0.01)])
    target_margin = FloatField('Target Margin %', validators=[DataRequired(), NumberRange(min=0, max=1000)])
    category = StringField('Category (optional)', validators=[Length(max=80)])
    submit = SubmitField('Save')
