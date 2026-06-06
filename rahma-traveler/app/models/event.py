# app/models/event.py
from app.extensions import db
from datetime import datetime, date
import pandas as pd

class CommunityEvent(db.Model):
    __tablename__ = 'community_events'
    
    # Primary Key
    event_id = db.Column(db.String(50), primary_key=True)
    
    # Core Fields
    event_name = db.Column(db.String(200), nullable=False)
    date = db.Column(db.Date)
    location = db.Column(db.String(200))
    status = db.Column(db.String(50)) # Upcoming / Completed / Cancelled
    description = db.Column(db.Text)
    notes = db.Column(db.Text)
    
    # Relationships
    ce_bookings = db.relationship('CEBooking', backref='event', lazy=True)

    def __repr__(self):
        return f'<CommunityEvent {self.event_id} - {self.event_name}>'

    @classmethod
    def from_excel_row(cls, row: dict):
        """Creates a CommunityEvent instance from a pandas DataFrame row."""
        def clean(val):
            if pd.isna(val):
                return None
            return val

        def to_date(val):
            val = clean(val)
            if val is None: return None
            if isinstance(val, (datetime, date)): return val
            try:
                return pd.to_datetime(val).date()
            except:
                return None

        return cls(
            event_id=clean(row.get("Event ID")),
            event_name=clean(row.get("Event Name")),
            date=to_date(row.get("Date")),
            location=clean(row.get("Location")),
            status=clean(row.get("Status")),
            description=clean(row.get("Description")),
            notes=clean(row.get("Notes"))
        )

    def to_dict(self):
        """Returns all fields as a JSON-serializable dict."""
        return {
            "event_id": self.event_id,
            "event_name": self.event_name,
            "date": self.date.isoformat() if self.date else None,
            "location": self.location,
            "status": self.status,
            "description": self.description,
            "notes": self.notes
        }
