# app/models/interaction.py
from app.extensions import db
from datetime import datetime, timezone
import pandas as pd
import sqlalchemy as sa


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

class Interaction(db.Model):
    __tablename__ = 'interactions'
    __table_args__ = (
        db.Index(
            "ux_interactions_channel_message_key",
            "channel",
            "message_key",
            unique=True,
            sqlite_where=sa.text("message_key IS NOT NULL AND message_key <> ''"),
            postgresql_where=sa.text("message_key IS NOT NULL AND message_key <> ''"),
        ),
    )
    
    # Primary Key
    interaction_id = db.Column(db.String(50), primary_key=True)
    
    # Core Fields
    timestamp = db.Column(db.DateTime, default=_utc_now)
    channel = db.Column(db.String(50))
    customer_name = db.Column(db.String(200))
    raw_phone = db.Column(db.String(50))
    integrated_whatsapp = db.Column(db.String(50))
    phone_lookup_key = db.Column(db.String(50))
    
    # Relationships/Keys
    traveler_id = db.Column(db.String(20), db.ForeignKey('travelers.traveler_id'))
    matched_row = db.Column(db.Integer)
    
    # State Info
    status_snapshot = db.Column(db.String(100))
    intent = db.Column(db.String(100))
    trip_type = db.Column(db.String(50))
    suggested_trips = db.Column(db.Text)
    action_taken = db.Column(db.Text)
    
    # Operational Info
    handoff_required = db.Column(db.Boolean, default=False)
    handoff_reason = db.Column(db.Text)
    agent_notes = db.Column(db.Text)
    
    # Automation/Flow Info
    flow_key = db.Column(db.String(100))
    step_key = db.Column(db.String(100))
    message_key = db.Column(db.String(100))
    language = db.Column(db.String(20))
    
    # Audit Info
    source_sheet = db.Column(db.String(100))
    source_row = db.Column(db.Integer)
    outcome = db.Column(db.Text)

    def __repr__(self):
        return f'<Interaction {self.interaction_id} - {self.customer_name}>'

    @classmethod
    def from_excel_row(cls, row: dict):
        """
        Creates an Interaction instance from a pandas DataFrame row (provided as dict).
        Handles NaN to None conversion.
        """
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

        def to_int(val):
            val = clean(val)
            if val is None: return 0
            try:
                return int(float(val))
            except:
                return 0

        def to_bool(val):
            val = clean(val)
            if val is None: return False
            if isinstance(val, bool): return val
            if str(val).lower() in ('true', 'yes', '1'): return True
            return False

        return cls(
            interaction_id=clean(row.get("Interaction ID")),
            timestamp=to_datetime(row.get("Timestamp")) or _utc_now(),
            channel=clean(row.get("Channel")),
            customer_name=clean(row.get("Customer Name")),
            raw_phone=clean(row.get("Raw Phone")),
            integrated_whatsapp=clean(row.get("Integrated WhatsApp")),
            phone_lookup_key=clean(row.get("Phone Lookup Key")),
            traveler_id=clean(row.get("Traveler ID")),
            matched_row=to_int(row.get("Matched Row")),
            status_snapshot=clean(row.get("Status Snapshot")),
            intent=clean(row.get("Intent")),
            trip_type=clean(row.get("Trip Type")),
            suggested_trips=clean(row.get("Suggested Trips")),
            action_taken=clean(row.get("Action Taken")),
            handoff_required=to_bool(row.get("Handoff Required")),
            handoff_reason=clean(row.get("Handoff Reason")),
            agent_notes=clean(row.get("Agent Notes")),
            flow_key=clean(row.get("Flow Key")),
            step_key=clean(row.get("Step Key")),
            message_key=clean(row.get("Message Key")),
            language=clean(row.get("Language")),
            source_sheet=clean(row.get("Source Sheet")),
            source_row=to_int(row.get("Source Row")),
            outcome=clean(row.get("Outcome"))
        )

    def to_dict(self):
        """Returns all fields as a JSON-serializable dict."""
        return {
            "interaction_id": self.interaction_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "channel": self.channel,
            "customer_name": self.customer_name,
            "raw_phone": self.raw_phone,
            "integrated_whatsapp": self.integrated_whatsapp,
            "phone_lookup_key": self.phone_lookup_key,
            "traveler_id": self.traveler_id,
            "matched_row": self.matched_row,
            "status_snapshot": self.status_snapshot,
            "intent": self.intent,
            "trip_type": self.trip_type,
            "suggested_trips": self.suggested_trips,
            "action_taken": self.action_taken,
            "handoff_required": self.handoff_required,
            "handoff_reason": self.handoff_reason,
            "agent_notes": self.agent_notes,
            "flow_key": self.flow_key,
            "step_key": self.step_key,
            "message_key": self.message_key,
            "language": self.language,
            "source_sheet": self.source_sheet,
            "source_row": self.source_row,
            "outcome": self.outcome
        }
