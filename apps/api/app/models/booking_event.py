from app.extensions import db
from datetime import datetime, timezone
import json


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BookingEventTrail(db.Model):
    __tablename__ = 'booking_event_trail'

    event_id = db.Column(db.String(50), primary_key=True)
    occurred_at = db.Column(db.DateTime, default=_utc_now)
    event_type = db.Column(db.String(100))
    event_label = db.Column(db.String(150))
    traveler_id = db.Column(db.String(20), db.ForeignKey('travelers.traveler_id'))
    lead_id = db.Column(db.String(50), db.ForeignKey('leads.lead_id'))
    booking_id = db.Column(db.String(50), db.ForeignKey('trip_bookings.booking_id'))
    trip_id = db.Column(db.String(50), db.ForeignKey('trips.trip_id'))
    interaction_id = db.Column(db.String(50), db.ForeignKey('interactions.interaction_id'))
    channel = db.Column(db.String(50))
    actor = db.Column(db.String(50))
    notes = db.Column(db.Text)
    metadata_json = db.Column(db.Text)

    # Declared purely so the unit of work knows an event INSERT depends on
    # its subject rows. The foreign key *columns* above are invisible to
    # SQLAlchemy's flush ordering -- only relationships create that
    # dependency -- so an event recorded in the same flush that creates its
    # booking could be inserted first and violate the constraint on any
    # backend that actually enforces one (Postgres always; SQLite only with
    # PRAGMA foreign_keys=ON). Many-to-one and backref-free on purpose: the
    # parents gain no collection, so deleting a traveler or booking still
    # behaves exactly as before, and the routes keep clearing event rows
    # explicitly.
    booking = db.relationship('TripBooking', foreign_keys=[booking_id])
    traveler = db.relationship('Traveler', foreign_keys=[traveler_id])
    trip = db.relationship('Trip', foreign_keys=[trip_id])
    lead = db.relationship('Lead', foreign_keys=[lead_id])
    interaction = db.relationship('Interaction', foreign_keys=[interaction_id])

    def metadata_dict(self):
        if not self.metadata_json:
            return {}
        try:
            return json.loads(self.metadata_json)
        except (TypeError, ValueError):
            return {}

    def to_dict(self):
        return {
            "event_id": self.event_id,
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at else None,
            "event_type": self.event_type,
            "event_label": self.event_label,
            "traveler_id": self.traveler_id,
            "lead_id": self.lead_id,
            "booking_id": self.booking_id,
            "trip_id": self.trip_id,
            "interaction_id": self.interaction_id,
            "channel": self.channel,
            "actor": self.actor,
            "notes": self.notes,
            "metadata": self.metadata_dict(),
        }
