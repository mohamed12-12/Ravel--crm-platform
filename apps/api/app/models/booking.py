# app/models/booking.py
from app.extensions import db
from datetime import datetime, timezone
import pandas as pd


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

class TripBooking(db.Model):
    __tablename__ = 'trip_bookings'
    
    # Primary Key
    booking_id = db.Column(db.String(50), primary_key=True)
    
    # Relationships/Keys
    trip_id = db.Column(db.String(50), db.ForeignKey('trips.trip_id'))
    trip_name = db.Column(db.String(200))
    traveler_id = db.Column(db.String(20), db.ForeignKey('travelers.traveler_id'))
    traveler_name = db.Column(db.String(200))
    
    # Booking Details
    room_type = db.Column(db.String(50))
    flight_option = db.Column(db.String(50))
    date_option = db.Column(db.String(50))
    currency = db.Column(db.String(20))
    group_size = db.Column(db.Integer, default=1)
    
    # Status/Audit
    booking_status = db.Column(db.String(50)) # Draft / Confirmed / Cancelled
    draft_created_at = db.Column(db.DateTime, default=_utc_now)
    booking_source = db.Column(db.String(100))
    lead_id = db.Column(db.String(50))
    interaction_id = db.Column(db.String(50))
    alert_id = db.Column(db.String(50))
    payment_status = db.Column(db.String(100))
    passport_required = db.Column(db.Boolean, default=False)
    passport_status = db.Column(db.String(50))
    booking_notes = db.Column(db.Text)

    def __repr__(self):
        return f'<TripBooking {self.booking_id} - {self.traveler_name}>'

    @classmethod
    def from_excel_row(cls, row: dict):
        """Creates a TripBooking instance from a pandas DataFrame row."""
        def clean(val):
            if pd.isna(val):
                return None
            return val

        def to_datetime(val):
            val = clean(val)
            if val is None: return None
            if isinstance(val, datetime): return val
            try:
                return pd.to_datetime(val)
            except:
                return None

        return cls(
            booking_id=clean(row.get("Booking ID")),
            trip_id=clean(row.get("Trip ID")),
            trip_name=clean(row.get("Trip Name")),
            traveler_id=clean(row.get("Traveler ID")),
            traveler_name=clean(row.get("Traveler Name")),
            room_type=clean(row.get("Room Type")),
            flight_option=clean(row.get("Flight Option")),
            date_option=clean(row.get("Date Option")),
            currency=clean(row.get("Currency")),
            group_size=int(clean(row.get("Group Size")) or 1),
            booking_status=clean(row.get("Booking Status")),
            draft_created_at=to_datetime(row.get("Draft Created At")) or _utc_now(),
            booking_source=clean(row.get("Booking Source")),
            lead_id=clean(row.get("Lead ID")),
            interaction_id=clean(row.get("Interaction ID")),
            alert_id=clean(row.get("Alert ID")),
            payment_status=clean(row.get("Payment Status")),
            passport_required=bool(clean(row.get("Passport Required"))) if clean(row.get("Passport Required")) is not None else False,
            passport_status=clean(row.get("Passport Status")),
            booking_notes=clean(row.get("Booking Notes"))
        )

    def to_dict(self):
        """Returns all fields as a JSON-serializable dict."""
        return {
            "booking_id": self.booking_id,
            "trip_id": self.trip_id,
            "trip_name": self.trip_name,
            "traveler_id": self.traveler_id,
            "traveler_name": self.traveler_name,
            "room_type": self.room_type,
            "flight_option": self.flight_option,
            "date_option": self.date_option,
            "currency": self.currency,
            "group_size": self.group_size or 1,
            "booking_status": self.booking_status,
            "draft_created_at": self.draft_created_at.isoformat() if self.draft_created_at else None,
            "booking_source": self.booking_source,
            "lead_id": self.lead_id,
            "interaction_id": self.interaction_id,
            "alert_id": self.alert_id,
            "payment_status": self.payment_status,
            "passport_required": bool(self.passport_required),
            "passport_status": self.passport_status,
            "booking_notes": self.booking_notes
        }

class CEBooking(db.Model):
    __tablename__ = 'ce_bookings'
    
    # Primary Key
    booking_id = db.Column(db.String(50), primary_key=True)
    
    # Relationships/Keys
    event_id = db.Column(db.String(50), db.ForeignKey('community_events.event_id'))
    event_name = db.Column(db.String(200))
    traveler_id = db.Column(db.String(20), db.ForeignKey('travelers.traveler_id'))
    traveler_name = db.Column(db.String(200))
    
    # Status/Audit
    status = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=_utc_now)
    notes = db.Column(db.Text)

    def __repr__(self):
        return f'<CEBooking {self.booking_id} - {self.traveler_name}>'

    @classmethod
    def from_excel_row(cls, row: dict):
        """Creates a CEBooking instance from a pandas DataFrame row."""
        def clean(val):
            if pd.isna(val):
                return None
            return val

        def to_datetime(val):
            val = clean(val)
            if val is None: return None
            if isinstance(val, datetime): return val
            try:
                return pd.to_datetime(val)
            except:
                return None

        return cls(
            booking_id=clean(row.get("Booking ID")),
            event_id=clean(row.get("Event ID")),
            event_name=clean(row.get("Event Name")),
            traveler_id=clean(row.get("Traveler ID")),
            traveler_name=clean(row.get("Traveler Name")),
            status=clean(row.get("Status")),
            created_at=to_datetime(row.get("Created At")) or _utc_now(),
            notes=clean(row.get("Notes"))
        )

    def to_dict(self):
        """Returns all fields as a JSON-serializable dict."""
        return {
            "booking_id": self.booking_id,
            "event_id": self.event_id,
            "event_name": self.event_name,
            "traveler_id": self.traveler_id,
            "traveler_name": self.traveler_name,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "notes": self.notes
        }
