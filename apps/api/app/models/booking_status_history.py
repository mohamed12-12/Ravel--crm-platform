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

    # Same reason as BookingEventTrail.booking: the foreign key column above
    # does not tell SQLAlchemy's unit of work that this row must be inserted
    # after the booking it points at, and booking_automation writes the
    # opening history row in the same flush that creates the booking. Left
    # undeclared, the two INSERTs could go out in either order -- harmless on
    # SQLite, a FOREIGN KEY violation on Postgres. Backref-free so the
    # existing explicit cleanup in the delete routes stays authoritative.
    booking = db.relationship('TripBooking', foreign_keys=[booking_id])
