from app.extensions import db
from datetime import datetime, timezone


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BookingStatusHistory(db.Model):
    __tablename__ = "booking_status_history"

    history_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    booking_id = db.Column(db.String(50), db.ForeignKey("trip_bookings.booking_id"), nullable=False)
    old_status = db.Column(db.String(50))
    new_status = db.Column(db.String(50))
    changed_at = db.Column(db.DateTime, default=_utc_now)
    changed_by = db.Column(db.String(50))
    change_source = db.Column(db.String(50))
    notes = db.Column(db.Text)
