# app/models/user.py
from app.extensions import db
from flask_login import UserMixin
from datetime import datetime, timezone


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    full_name = db.Column(db.String(200))
    password_hash = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(120), unique=True)
    role = db.Column(db.String(50), nullable=False, default='agent', index=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=_utc_now)
    updated_at = db.Column(db.DateTime, default=_utc_now, onupdate=_utc_now)
    last_login_at = db.Column(db.DateTime)

    assigned_leads = db.relationship(
        'Lead',
        foreign_keys='Lead.assigned_to_user_id',
        back_populates='assigned_user',
        lazy='dynamic',
    )
    assigned_bookings = db.relationship(
        'TripBooking',
        foreign_keys='TripBooking.assigned_to_user_id',
        back_populates='assigned_user',
        lazy='dynamic',
    )

    @property
    def display_name(self) -> str:
        return (self.full_name or self.username or '').strip() or f'User {self.id}'

    def set_password(self, password: str) -> None:
        from werkzeug.security import generate_password_hash

        self.password_hash = generate_password_hash(password)

    def __repr__(self):
        return f'<User {self.username} ({self.role})>'
