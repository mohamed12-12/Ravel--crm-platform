from datetime import datetime, timezone

from app.extensions import db


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AssignmentHistory(db.Model):
    __tablename__ = 'assignment_history'

    id = db.Column(db.Integer, primary_key=True)
    resource_type = db.Column(db.String(30), nullable=False, index=True)
    resource_id = db.Column(db.String(80), nullable=False, index=True)
    previous_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    new_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    assigned_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    reason = db.Column(db.Text)
    request_id = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=_utc_now, nullable=False, index=True)

    previous_user = db.relationship('User', foreign_keys=[previous_user_id])
    new_user = db.relationship('User', foreign_keys=[new_user_id])
    assigned_by_user = db.relationship('User', foreign_keys=[assigned_by_user_id])

    __table_args__ = (
        db.Index('ix_assignment_history_resource', 'resource_type', 'resource_id'),
    )
