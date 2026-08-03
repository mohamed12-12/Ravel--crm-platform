# app/models/lead.py
from app.extensions import db
from datetime import datetime, date, timezone
import pandas as pd


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

class Lead(db.Model):
    __tablename__ = 'leads'
    
    # Primary Key
    lead_id = db.Column(db.String(50), primary_key=True)
    
    # Metadata
    created_at = db.Column(db.DateTime, default=_utc_now)
    updated_at = db.Column(db.DateTime, default=_utc_now, onupdate=_utc_now)
    
    # Customer Info
    customer_name = db.Column(db.String(200))
    raw_phone = db.Column(db.String(50))
    integrated_whatsapp = db.Column(db.String(50))
    phone_lookup_key = db.Column(db.String(50))
    
    # Relationships/Keys
    traveler_id = db.Column(db.String(20), db.ForeignKey('travelers.traveler_id'))
    traveler_status = db.Column(db.String(50))
    customer_tier = db.Column(db.String(50))
    match_status = db.Column(db.String(50))
    
    # Pipeline Info
    lead_stage = db.Column(db.String(100))
    lead_source = db.Column(db.String(100))
    channel = db.Column(db.String(50))
    preferred_trip_type = db.Column(db.String(50))
    group_size = db.Column(db.Integer, default=1)
    interested_trip_ids = db.Column(db.Text)
    suggested_trip_ids = db.Column(db.Text)
    priority = db.Column(db.String(50))
    follow_up_status = db.Column(db.String(100))
    follow_up_due_date = db.Column(db.Date)
    assigned_to = db.Column(db.String(100))
    assigned_to_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    assigned_at = db.Column(db.DateTime)
    assigned_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), index=True)
    idempotency_key = db.Column(db.Text)
    last_contact_at = db.Column(db.DateTime)
    customer_response_status = db.Column(db.String(100))

    assigned_user = db.relationship(
        'User',
        foreign_keys=[assigned_to_user_id],
        back_populates='assigned_leads',
    )
    assigned_by_user = db.relationship('User', foreign_keys=[assigned_by_user_id])
    
    # Operational Info
    last_interaction_id = db.Column(db.String(50))
    interaction_count = db.Column(db.Integer, default=0)
    handoff_required = db.Column(db.Boolean, default=False)
    handoff_reason = db.Column(db.Text)
    notes = db.Column(db.Text)
    passport_attachment_ref = db.Column(db.Text)
    passport_status = db.Column(db.String(50))
    
    # Automation Flow Info
    flow_key = db.Column(db.String(100))
    current_step = db.Column(db.String(100))
    language = db.Column(db.String(20))
    trigger_keyword = db.Column(db.String(100))
    waitlist_id = db.Column(db.String(50))
    handoff_id = db.Column(db.String(50))
    booking_id = db.Column(db.String(50))
    
    # Audit Info
    source_sheet = db.Column(db.String(100))
    source_row = db.Column(db.Integer)

    def __repr__(self):
        return f'<Lead {self.lead_id} - {self.customer_name}>'

    @classmethod
    def from_excel_row(cls, row: dict):
        """
        Creates a Lead instance from a pandas DataFrame row (provided as dict).
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

        def to_date(val):
            val = clean(val)
            if val is None: return None
            if isinstance(val, (datetime, date)): return val
            try:
                return pd.to_datetime(val).date()
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
            lead_id=clean(row.get("Lead ID")),
            created_at=to_datetime(row.get("Created At")) or _utc_now(),
            updated_at=to_datetime(row.get("Updated At")) or _utc_now(),
            customer_name=clean(row.get("Customer Name")),
            raw_phone=clean(row.get("Raw Phone")),
            integrated_whatsapp=clean(row.get("Integrated WhatsApp")),
            phone_lookup_key=clean(row.get("Phone Lookup Key")),
            traveler_id=clean(row.get("Traveler ID")),
            traveler_status=clean(row.get("Traveler Status")),
            customer_tier=clean(row.get("Customer Tier")),
            match_status=clean(row.get("Match Status")),
            lead_stage=clean(row.get("Lead Stage")),
            lead_source=clean(row.get("Lead Source")),
            channel=clean(row.get("Channel")),
            preferred_trip_type=clean(row.get("Preferred Trip Type")),
            group_size=to_int(row.get("Group Size")) or 1,
            interested_trip_ids=clean(row.get("Interested Trip IDs")),
            suggested_trip_ids=clean(row.get("Suggested Trip IDs")),
            priority=clean(row.get("Priority")),
            follow_up_status=clean(row.get("Follow Up Status")),
            follow_up_due_date=to_date(row.get("Follow Up Due Date")),
            assigned_to=clean(row.get("Assigned To")),
            assigned_to_user_id=None,
            assigned_at=None,
            assigned_by_user_id=None,
            last_contact_at=to_datetime(row.get("Last Contact At")),
            customer_response_status=clean(row.get("Customer Response Status")),
            last_interaction_id=clean(row.get("Last Interaction ID")),
            interaction_count=to_int(row.get("Interaction Count")),
            handoff_required=to_bool(row.get("Handoff Required")),
            handoff_reason=clean(row.get("Handoff Reason")),
            notes=clean(row.get("Notes")),
            passport_attachment_ref=clean(row.get("Passport Attachment Ref")),
            passport_status=clean(row.get("Passport Status")),
            flow_key=clean(row.get("Flow Key")),
            current_step=clean(row.get("Current Step")),
            language=clean(row.get("Language")),
            trigger_keyword=clean(row.get("Trigger Keyword")),
            waitlist_id=clean(row.get("Waitlist ID")),
            handoff_id=clean(row.get("Handoff ID")),
            booking_id=clean(row.get("Booking ID")),
            source_sheet=clean(row.get("Source Sheet")),
            source_row=to_int(row.get("Source Row"))
        )

    def to_dict(self):
        """Returns all fields as a JSON-serializable dict."""
        return {
            "lead_id": self.lead_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "customer_name": self.customer_name,
            "raw_phone": self.raw_phone,
            "integrated_whatsapp": self.integrated_whatsapp,
            "phone_lookup_key": self.phone_lookup_key,
            "traveler_id": self.traveler_id,
            "traveler_status": self.traveler_status,
            "customer_tier": self.customer_tier,
            "match_status": self.match_status,
            "lead_stage": self.lead_stage,
            "lead_source": self.lead_source,
            "channel": self.channel,
            "preferred_trip_type": self.preferred_trip_type,
            "group_size": self.group_size or 1,
            "interested_trip_ids": self.interested_trip_ids,
            "suggested_trip_ids": self.suggested_trip_ids,
            "priority": self.priority,
            "follow_up_status": self.follow_up_status,
            "follow_up_due_date": self.follow_up_due_date.isoformat() if self.follow_up_due_date else None,
            "assigned_to": self.assigned_to,
            "assigned_to_user_id": self.assigned_to_user_id,
            "assigned_user": self.assigned_user.display_name if self.assigned_user else None,
            "assigned_at": self.assigned_at.isoformat() if self.assigned_at else None,
            "assigned_by_user_id": self.assigned_by_user_id,
            "idempotency_key": self.idempotency_key,
            "last_contact_at": self.last_contact_at.isoformat() if self.last_contact_at else None,
            "customer_response_status": self.customer_response_status,
            "last_interaction_id": self.last_interaction_id,
            "interaction_count": self.interaction_count,
            "handoff_required": self.handoff_required,
            "handoff_reason": self.handoff_reason,
            "notes": self.notes,
            "passport_attachment_ref": self.passport_attachment_ref,
            "passport_status": self.passport_status,
            "flow_key": self.flow_key,
            "current_step": self.current_step,
            "language": self.language,
            "trigger_keyword": self.trigger_keyword,
            "waitlist_id": self.waitlist_id,
            "handoff_id": self.handoff_id,
            "booking_id": self.booking_id,
            "source_sheet": self.source_sheet,
            "source_row": self.source_row
        }
