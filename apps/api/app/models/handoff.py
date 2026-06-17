# app/models/handoff.py
from app.extensions import db
from datetime import datetime
import pandas as pd

class HandoffQueue(db.Model):
    __tablename__ = 'handoff_queue'
    
    # Primary Key
    handoff_id = db.Column(db.String(50), primary_key=True)
    
    # Meta
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Keys
    lead_id = db.Column(db.String(50), db.ForeignKey('leads.lead_id'))
    traveler_id = db.Column(db.String(20), db.ForeignKey('travelers.traveler_id'))
    trip_id = db.Column(db.String(50), db.ForeignKey('trips.trip_id'))
    
    # Details
    flow_key = db.Column(db.String(100))
    reason = db.Column(db.Text)
    priority = db.Column(db.String(50)) # High / Medium / Low
    channel = db.Column(db.String(50))
    status = db.Column(db.String(50), default='Pending') # Pending / In Progress / Resolved
    owner = db.Column(db.String(100))
    assigned_to = db.Column(db.String(100))
    notes = db.Column(db.Text)

    def __repr__(self):
        return f'<Handoff {self.handoff_id} - {self.reason}>'

    @classmethod
    def from_excel_row(cls, row: dict):
        """Creates a HandoffQueue instance from a pandas DataFrame row."""
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
            handoff_id=clean(row.get("Handoff ID")),
            created_at=to_datetime(row.get("Created At")) or datetime.utcnow(),
            lead_id=clean(row.get("Lead ID")),
            traveler_id=clean(row.get("Traveler ID")),
            trip_id=clean(row.get("Trip ID")),
            flow_key=clean(row.get("Flow Key")),
            reason=clean(row.get("Reason")),
            priority=clean(row.get("Priority")),
            channel=clean(row.get("Channel")),
            status=clean(row.get("Status")),
            owner=clean(row.get("Owner")),
            assigned_to=clean(row.get("Assigned To")),
            notes=clean(row.get("Notes"))
        )

    def to_dict(self):
        """Returns all fields as a JSON-serializable dict."""
        return {
            "handoff_id": self.handoff_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "lead_id": self.lead_id,
            "traveler_id": self.traveler_id,
            "trip_id": self.trip_id,
            "flow_key": self.flow_key,
            "reason": self.reason,
            "priority": self.priority,
            "channel": self.channel,
            "status": self.status,
            "owner": self.owner,
            "assigned_to": self.assigned_to,
            "notes": self.notes
        }
