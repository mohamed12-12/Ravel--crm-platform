from datetime import datetime, timezone

from app.extensions import db


class UserAuditLog(db.Model):
    __tablename__ = 'user_audit_log'

    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    target_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    action = db.Column(db.String(80), nullable=False)
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    actor = db.relationship('User', foreign_keys=[actor_user_id])
    target = db.relationship('User', foreign_keys=[target_user_id])
