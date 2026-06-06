# app/models/trip.py
from app.extensions import db
from datetime import datetime, date
import pandas as pd

class Trip(db.Model):
    __tablename__ = 'trips'
    
    # Primary Key
    trip_id = db.Column(db.String(50), primary_key=True)
    
    # Core Fields
    trip_name = db.Column(db.String(200), nullable=False)
    type = db.Column(db.String(50)) # Local / International
    year = db.Column(db.Integer)
    trip_leader = db.Column(db.String(100))
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    sales_status = db.Column(db.String(50)) # Open / Closed / Cancelled
    data_audit = db.Column(db.Text)
    
    # Extension Fields
    trip_window_status = db.Column(db.String(50))
    trip_availability_note = db.Column(db.Text)
    next_reengage_date = db.Column(db.Date)
    
    # Inventory / Capacity Fields
    single_total = db.Column(db.Integer, default=0)
    double_total = db.Column(db.Integer, default=0)
    triple_total = db.Column(db.Integer, default=0)
    
    single_remaining = db.Column(db.Integer, default=0)
    double_remaining = db.Column(db.Integer, default=0)
    triple_remaining = db.Column(db.Integer, default=0)
    
    draft_holds_single = db.Column(db.Integer, default=0)
    draft_holds_double = db.Column(db.Integer, default=0)
    draft_holds_triple = db.Column(db.Integer, default=0)
    
    # Content Fields
    public_price = db.Column(db.String(200))
    public_description = db.Column(db.Text)
    sales_notes = db.Column(db.Text)

    # Relationships
    bookings = db.relationship('TripBooking', backref='trip', lazy=True)

    def __repr__(self):
        return f'<Trip {self.trip_id} - {self.trip_name}>'

    @classmethod
    def from_excel_row(cls, row: dict):
        """
        Creates a Trip instance from a pandas DataFrame row (provided as dict).
        Handles NaN to None conversion.
        """
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

        def to_int(val):
            val = clean(val)
            if val is None: return 0
            try:
                return int(float(val))
            except:
                return 0

        return cls(
            trip_id=clean(row.get("Trip ID")),
            trip_name=clean(row.get("Trip Name")),
            type=clean(row.get("Type")),
            year=to_int(row.get("Year")),
            trip_leader=clean(row.get("Trip Leader")),
            start_date=to_date(row.get("Start Date")),
            end_date=to_date(row.get("End Date")),
            sales_status=clean(row.get("Sales Status")),
            data_audit=clean(row.get("Data Audit")),
            trip_window_status=clean(row.get("Trip Window Status")),
            trip_availability_note=clean(row.get("Trip Availability Note")),
            next_reengage_date=to_date(row.get("Next Reengage Date")),
            single_total=to_int(row.get("Single Total")),
            double_total=to_int(row.get("Double Total")),
            triple_total=to_int(row.get("Triple Total")),
            single_remaining=to_int(row.get("Single Remaining")),
            double_remaining=to_int(row.get("Double Remaining")),
            triple_remaining=to_int(row.get("Triple Remaining")),
            draft_holds_single=to_int(row.get("Draft Holds Single")),
            draft_holds_double=to_int(row.get("Draft Holds Double")),
            draft_holds_triple=to_int(row.get("Draft Holds Triple")),
            public_price=clean(row.get("Public Price")),
            public_description=clean(row.get("Public Description")),
            sales_notes=clean(row.get("Sales Notes"))
        )

    def to_dict(self):
        """Returns all fields as a JSON-serializable dict."""
        return {
            "trip_id": self.trip_id,
            "trip_name": self.trip_name,
            "type": self.type,
            "year": self.year,
            "trip_leader": self.trip_leader,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "sales_status": self.sales_status,
            "data_audit": self.data_audit,
            "trip_window_status": self.trip_window_status,
            "trip_availability_note": self.trip_availability_note,
            "next_reengage_date": self.next_reengage_date.isoformat() if self.next_reengage_date else None,
            "single_total": self.single_total,
            "double_total": self.double_total,
            "triple_total": self.triple_total,
            "single_remaining": self.single_remaining,
            "double_remaining": self.double_remaining,
            "triple_remaining": self.triple_remaining,
            "draft_holds_single": self.draft_holds_single,
            "draft_holds_double": self.draft_holds_double,
            "draft_holds_triple": self.draft_holds_triple,
            "public_price": self.public_price,
            "public_description": self.public_description,
            "sales_notes": self.sales_notes
        }
