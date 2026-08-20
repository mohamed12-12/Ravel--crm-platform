from __future__ import annotations

from datetime import datetime

from app.extensions import db
from services.crm.system_services.private_trips import (
    consultation_due_from,
    normalize_private_service_type,
    normalize_private_stage,
    utc_now,
)


def _naive(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=None) if isinstance(value, datetime) and value.tzinfo else value


class PrivateTripRequest(db.Model):
    __tablename__ = "private_trip_requests"

    request_id = db.Column(db.String(20), primary_key=True)
    traveler_id = db.Column(db.String(20), db.ForeignKey("travelers.traveler_id"), index=True)
    lead_id = db.Column(db.String(50), db.ForeignKey("leads.lead_id"), index=True)

    service_type = db.Column(db.String(40), nullable=False)
    trip_scope = db.Column(db.String(20), nullable=False)

    destination = db.Column(db.String(200))
    start_date_pref = db.Column(db.Date)
    end_date_pref = db.Column(db.Date)
    dates_flexible = db.Column(db.Boolean, nullable=False, default=False)

    party_size = db.Column(db.Integer, nullable=False, default=1)
    boys_count = db.Column(db.Integer, nullable=False, default=0)
    girls_count = db.Column(db.Integer, nullable=False, default=0)

    budget_amount = db.Column(db.Float)
    budget_currency = db.Column(db.String(3))

    stage = db.Column(db.String(40), nullable=False, default="registered", index=True)
    stage_changed_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    assigned_to_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    assigned_to = db.Column(db.String(100))
    assigned_at = db.Column(db.DateTime)
    assigned_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)

    # Employee follow-up bookkeeping -- distinct from `stage` (the
    # consultation/design/deposit pipeline milestone): these track whether an
    # employee is actively on top of the request, mirroring leads.py's
    # equivalent fields so private requests get the same "Employee
    # Follow-up" panel leads already have.
    priority = db.Column(db.String(50), default="Medium")
    current_step = db.Column(db.String(200))
    channel = db.Column(db.String(50))
    follow_up_status = db.Column(db.String(100))
    follow_up_due_date = db.Column(db.Date)
    last_contact_at = db.Column(db.DateTime)
    customer_response_status = db.Column(db.String(100))

    consultation_due_at = db.Column(db.DateTime)
    consultation_done_at = db.Column(db.DateTime)
    design_due_at = db.Column(db.DateTime)
    design_delivered_at = db.Column(db.DateTime)

    deposit_amount = db.Column(db.Float)
    deposit_currency = db.Column(db.String(3))
    deposit_paid_at = db.Column(db.DateTime)
    deposit_is_refundable = db.Column(db.Boolean, nullable=False, default=False)

    converted_booking_id = db.Column(db.String(50), db.ForeignKey("trip_bookings.booking_id"), index=True)
    lost_reason = db.Column(db.Text)

    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now, index=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    idempotency_key = db.Column(db.String(200), unique=True, index=True)

    traveler = db.relationship("Traveler", foreign_keys=[traveler_id])
    lead = db.relationship("Lead", foreign_keys=[lead_id])
    assigned_user = db.relationship("User", foreign_keys=[assigned_to_user_id])
    assigned_by_user = db.relationship("User", foreign_keys=[assigned_by_user_id])
    converted_booking = db.relationship("TripBooking", foreign_keys=[converted_booking_id])

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.stage:
            self.stage = "registered"
        self.stage = normalize_private_stage(self.stage) or "registered"
        self.service_type = normalize_private_service_type(self.service_type) or self.service_type
        if not self.created_at:
            self.created_at = utc_now()
        if not self.stage_changed_at:
            self.stage_changed_at = utc_now()
        if not self.consultation_due_at:
            self.consultation_due_at = consultation_due_from(self.created_at)

    @property
    def is_consultation_overdue(self) -> bool:
        return bool(
            self.consultation_due_at
            and not self.consultation_done_at
            and self.stage not in {"converted", "lost"}
            and _naive(self.consultation_due_at) < utc_now().replace(tzinfo=None)
        )

    @property
    def is_design_overdue(self) -> bool:
        return bool(
            self.design_due_at
            and not self.design_delivered_at
            and self.stage not in {"converted", "lost"}
            and _naive(self.design_due_at) < utc_now().replace(tzinfo=None)
        )

    def mark_stage(self, stage: str, *, when: datetime | None = None) -> None:
        target = normalize_private_stage(stage)
        if not target:
            raise ValueError("Invalid private request stage.")
        self.stage = target
        self.stage_changed_at = when or utc_now()
        if target == "consultation_done" and not self.consultation_done_at:
            self.consultation_done_at = self.stage_changed_at
        if target == "deposit_paid" and not self.deposit_paid_at:
            self.deposit_paid_at = self.stage_changed_at
        if target == "design_delivered" and not self.design_delivered_at:
            self.design_delivered_at = self.stage_changed_at

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "traveler_id": self.traveler_id,
            "lead_id": self.lead_id,
            "service_type": self.service_type,
            "trip_scope": self.trip_scope,
            "destination": self.destination,
            "start_date_pref": self.start_date_pref.isoformat() if self.start_date_pref else None,
            "end_date_pref": self.end_date_pref.isoformat() if self.end_date_pref else None,
            "dates_flexible": bool(self.dates_flexible),
            "party_size": self.party_size,
            "boys_count": self.boys_count,
            "girls_count": self.girls_count,
            "budget_amount": self.budget_amount,
            "budget_currency": self.budget_currency,
            "stage": self.stage,
            "stage_changed_at": self.stage_changed_at.isoformat() if self.stage_changed_at else None,
            "assigned_to_user_id": self.assigned_to_user_id,
            "assigned_to": self.assigned_to,
            "assigned_at": self.assigned_at.isoformat() if self.assigned_at else None,
            "assigned_by_user_id": self.assigned_by_user_id,
            "priority": self.priority,
            "current_step": self.current_step,
            "channel": self.channel,
            "follow_up_status": self.follow_up_status,
            "follow_up_due_date": self.follow_up_due_date.isoformat() if self.follow_up_due_date else None,
            "last_contact_at": self.last_contact_at.isoformat() if self.last_contact_at else None,
            "customer_response_status": self.customer_response_status,
            "consultation_due_at": self.consultation_due_at.isoformat() if self.consultation_due_at else None,
            "consultation_done_at": self.consultation_done_at.isoformat() if self.consultation_done_at else None,
            "design_due_at": self.design_due_at.isoformat() if self.design_due_at else None,
            "design_delivered_at": self.design_delivered_at.isoformat() if self.design_delivered_at else None,
            "deposit_amount": self.deposit_amount,
            "deposit_currency": self.deposit_currency,
            "deposit_paid_at": self.deposit_paid_at.isoformat() if self.deposit_paid_at else None,
            "deposit_is_refundable": bool(self.deposit_is_refundable),
            "converted_booking_id": self.converted_booking_id,
            "lost_reason": self.lost_reason,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": self.created_by,
            "idempotency_key": self.idempotency_key,
        }
