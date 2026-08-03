# app/models/traveler.py
from app.extensions import db
from datetime import datetime, date, timezone
import pandas as pd


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

class Traveler(db.Model):
    __tablename__ = 'travelers'
    
    # Primary Key
    traveler_id = db.Column(db.String(20), primary_key=True) # TR001 format
    
    # Fields from Excel "Travelers" sheet
    status = db.Column(db.String(50))
    full_name = db.Column(db.String(200), nullable=False)
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    birthday = db.Column(db.Date)
    gender = db.Column(db.String(20))
    nationality = db.Column(db.String(100))
    phone_code = db.Column(db.String(10))
    whatsapp_raw = db.Column(db.String(50))
    email = db.Column(db.String(150))
    community_whatsapp = db.Column(db.String(100))
    residence = db.Column(db.String(150))
    local_trips_count = db.Column(db.Integer, default=0)
    international_trips_count = db.Column(db.Integer, default=0)
    total_trips = db.Column(db.Integer, default=0)
    community_events_count = db.Column(db.Integer, default=0)
    lifetime_revenue = db.Column(db.Float, default=0.0)
    preferred_currency = db.Column(db.String(20))
    notes = db.Column(db.Text)
    introduce_yourself = db.Column(db.Text)
    emergency_contact = db.Column(db.String(200))
    emergency_phone = db.Column(db.String(50))
    medical_notes = db.Column(db.Text)
    room_preference = db.Column(db.String(100))
    rating = db.Column(db.Float) # 1-5
    integrated_whatsapp = db.Column(db.String(50))
    normalized_whatsapp = db.Column(db.String(50))
    phone_lookup_key = db.Column(db.String(50))
    lead_source = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=_utc_now)
    last_contacted_at = db.Column(db.DateTime)
    agent_notes = db.Column(db.Text)
    data_audit = db.Column(db.Text)
    primary_language = db.Column(db.String(20))
    current_flow_key = db.Column(db.String(100))
    current_step = db.Column(db.String(100))
    last_lead_id = db.Column(db.String(50))
    last_booking_id = db.Column(db.String(50))

    # Passport and attachments fields for international trips
    passport_name = db.Column(db.String(200))
    passport_number = db.Column(db.String(50))
    passport_expiry = db.Column(db.Date)
    passport_nationality = db.Column(db.String(100))
    passport_attachment_ref = db.Column(db.String(300))

    # Relationships
    documents = db.relationship('TravelerDocument', backref='traveler_record', lazy=True, cascade='all, delete-orphan')
    trip_bookings = db.relationship('TripBooking', backref='traveler', lazy=True)
    ce_bookings = db.relationship('CEBooking', backref='traveler', lazy=True)
    leads = db.relationship('Lead', backref='traveler', lazy=True)
    interactions = db.relationship('Interaction', backref='traveler', lazy=True)

    def __repr__(self):
        return f'<Traveler {self.traveler_id} - {self.full_name}>'

    @classmethod
    def from_excel_row(cls, row: dict):
        """
        Creates a Traveler instance from a pandas DataFrame row (provided as dict).
        Handles NaN to None conversion.
        """
        def clean(val):
            if pd.isna(val):
                return None
            return val

        normalized_row = {
            str(key).strip().lower(): value
            for key, value in row.items()
            if key is not None
        }

        def get(*headers):
            for header in headers:
                value = row.get(header)
                if value is not None:
                    return value
                value = normalized_row.get(str(header).strip().lower())
                if value is not None:
                    return value
            return None

        def to_int(val):
            val = clean(val)
            if val in (None, ""):
                return 0
            try:
                return int(float(str(val).replace(",", "").strip()))
            except (TypeError, ValueError):
                return 0

        def to_float(val):
            val = clean(val)
            if val in (None, ""):
                return 0.0
            try:
                return float(str(val).replace(",", "").replace("$", "").strip())
            except (TypeError, ValueError):
                return 0.0

        # Handle potential Date/Datetime conversions from Excel strings/objects
        def to_date(val):
            val = clean(val)
            if val is None: return None
            if isinstance(val, (datetime, date)): return val
            try:
                return pd.to_datetime(val).date()
            except:
                return None

        def to_datetime(val):
            val = clean(val)
            if val is None: return None
            if isinstance(val, datetime): return val
            try:
                return pd.to_datetime(val)
            except:
                return None

        return cls(
            traveler_id=clean(get("Traveler ID")),
            status=clean(get("Status")),
            full_name=clean(get("Full Name")),
            first_name=clean(get("First Name")),
            last_name=clean(get("Last Name")),
            birthday=to_date(get("Birthday")),
            gender=clean(get("Gender")),
            nationality=clean(get("Nationality")),
            phone_code=clean(get("Phone Code", "Code")),
            whatsapp_raw=clean(get("WhatsApp (raw)", "WhatsApp")),
            email=clean(get("Email")),
            community_whatsapp=clean(get("Community WhatsApp (group)", "Community Whatsapp", "Community Whats")),
            residence=clean(get("Residence")),
            local_trips_count=to_int(get("Local Trips Count", "Loc. Trips")),
            international_trips_count=to_int(get("International Trips Count", "Int. Trips")),
            total_trips=to_int(get("Total Trips", "Total trips")),
            community_events_count=to_int(get("Community Events Count", "Comm. Events")),
            lifetime_revenue=to_float(get("Lifetime Revenue")),
            preferred_currency=clean(get("Preferred Currency")),
            notes=clean(get("Notes")),
            introduce_yourself=clean(get("Introduce Yourself", "Introduce yourself")),
            emergency_contact=clean(get("Emergency Contact")),
            emergency_phone=clean(get("Emergency Phone")),
            medical_notes=clean(get("Medical Notes")),
            room_preference=clean(get("Room Preference")),
            rating=clean(get("Rating", "⭐ Rating (1–5)")),
            integrated_whatsapp=clean(get("Integrated WhatsApp (normalized)", "Integrated WhatsApp")),
            normalized_whatsapp=clean(get("Normalized WhatsApp")),
            phone_lookup_key=clean(get("Phone Lookup Key")),
            lead_source=clean(get("Lead Source")),
            created_at=to_datetime(get("Created At")) or _utc_now(),
            last_contacted_at=to_datetime(get("Last Contacted At")),
            agent_notes=clean(get("Agent Notes")),
            data_audit=clean(get("Data Audit")),
            primary_language=clean(get("Primary Language")),
            current_flow_key=clean(get("Current Flow Key")),
            current_step=clean(get("Current Step")),
            last_lead_id=clean(get("Last Lead ID")),
            last_booking_id=clean(get("Last Booking ID")),
            passport_name=clean(get("Passport Name")),
            passport_number=clean(get("Passport Number")),
            passport_expiry=to_date(get("Passport Expiry Date")),
            passport_nationality=clean(get("Passport Nationality")),
            passport_attachment_ref=clean(get("Passport Image")),
        )

    def to_dict(self):
        """Returns all fields as a JSON-serializable dict."""
        return {
            "traveler_id": self.traveler_id,
            "status": self.status,
            "full_name": self.full_name,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "birthday": self.birthday.isoformat() if self.birthday else None,
            "gender": self.gender,
            "nationality": self.nationality,
            "phone_code": self.phone_code,
            "whatsapp_raw": self.whatsapp_raw,
            "email": self.email,
            "community_whatsapp": self.community_whatsapp,
            "residence": self.residence,
            "local_trips_count": self.local_trips_count,
            "international_trips_count": self.international_trips_count,
            "total_trips": self.total_trips,
            "community_events_count": self.community_events_count,
            "lifetime_revenue": self.lifetime_revenue,
            "preferred_currency": self.preferred_currency,
            "notes": self.notes,
            "introduce_yourself": self.introduce_yourself,
            "emergency_contact": self.emergency_contact,
            "emergency_phone": self.emergency_phone,
            "medical_notes": self.medical_notes,
            "room_preference": self.room_preference,
            "rating": self.rating,
            "integrated_whatsapp": self.integrated_whatsapp,
            "normalized_whatsapp": self.normalized_whatsapp,
            "phone_lookup_key": self.phone_lookup_key,
            "lead_source": self.lead_source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_contacted_at": self.last_contacted_at.isoformat() if self.last_contacted_at else None,
            "agent_notes": self.agent_notes,
            "data_audit": self.data_audit,
            "primary_language": self.primary_language,
            "current_flow_key": self.current_flow_key,
            "current_step": self.current_step,
            "last_lead_id": self.last_lead_id,
            "last_booking_id": self.last_booking_id,
            "passport_name": self.passport_name,
            "passport_number": self.passport_number,
            "passport_expiry": self.passport_expiry.isoformat() if self.passport_expiry else None,
            "passport_nationality": self.passport_nationality,
            "passport_attachment_ref": self.passport_attachment_ref,
        }
