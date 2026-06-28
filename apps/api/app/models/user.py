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
    password_hash = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(120), unique=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=_utc_now)

    def __repr__(self):
        return f'<User {self.username}>'
