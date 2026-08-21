# app/models/additional_fee.py
"""Employee-added additional fees on a trip booking or a private trip request.

Until now a booking was worth exactly its trip's room price x party size:
there was nowhere to record the visa an employee arranged, the insurance
policy, the airport transfer or the room upgrade, so that money was either
lost or smuggled into the notes field, and Revenue Analytics never saw it.

Design notes:

* **One table, two parents.** A fee belongs to a booking or to a private trip
  request, never both and never neither (enforced by a CHECK constraint). Two
  tables would mean duplicating the validation, the void mechanics and every
  revenue read -- and the two would drift, which is how the money surfaces in
  this codebase have gone wrong before.

* **Voided, not deleted.** Removing a fee sets `voided_at`/`void_reason` and
  leaves the row. A fee that was quoted and then dropped is part of the
  record, and last month's revenue must not silently change because somebody
  tidied a row this month. `is_active` is the only thing revenue reads.

* **Currency is the parent's.** Enforced on write by
  services/crm/system_services/fee_rules.validate_fee_currency, because
  nothing in this system converts between EGP and USD.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db
from services.crm.system_services.fee_rules import fee_category_label


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AdditionalFee(db.Model):
    __tablename__ = "additional_fees"

    fee_id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    booking_id = db.Column(db.String(50), db.ForeignKey("trip_bookings.booking_id"), index=True)
    private_request_id = db.Column(
        db.String(20), db.ForeignKey("private_trip_requests.request_id"), index=True
    )
    traveler_id = db.Column(db.String(20), db.ForeignKey("travelers.traveler_id"), index=True)

    label = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(40), nullable=False, default="other")
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_by = db.Column(db.String(100))

    voided_at = db.Column(db.DateTime)
    voided_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    void_reason = db.Column(db.Text)

    booking = db.relationship("TripBooking", foreign_keys=[booking_id])
    private_request = db.relationship("PrivateTripRequest", foreign_keys=[private_request_id])
    traveler = db.relationship("Traveler", foreign_keys=[traveler_id])
    created_by_user = db.relationship("User", foreign_keys=[created_by_user_id])
    voided_by_user = db.relationship("User", foreign_keys=[voided_by_user_id])

    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_additional_fees_amount_positive"),
        db.CheckConstraint(
            "(booking_id IS NOT NULL AND private_request_id IS NULL) "
            "OR (booking_id IS NULL AND private_request_id IS NOT NULL)",
            name="ck_additional_fees_exactly_one_parent",
        ),
    )

    def __repr__(self) -> str:
        parent = self.booking_id or self.private_request_id or "?"
        return f"<AdditionalFee {self.fee_id} {parent} {self.amount} {self.currency}>"

    @property
    def is_active(self) -> bool:
        """Whether this fee still counts. The only thing revenue asks."""
        return self.voided_at is None

    @property
    def category_label(self) -> str:
        return fee_category_label(self.category)

    @property
    def public_ref(self) -> str:
        return f"FEE-{int(self.fee_id):06d}" if self.fee_id else "FEE-new"

    def to_dict(self) -> dict:
        return {
            "fee_id": self.fee_id,
            "public_ref": self.public_ref,
            "booking_id": self.booking_id,
            "private_request_id": self.private_request_id,
            "traveler_id": self.traveler_id,
            "label": self.label,
            "category": self.category,
            "category_label": self.category_label,
            "amount": self.amount,
            "currency": self.currency,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by_user_id": self.created_by_user_id,
            "created_by": self.created_by,
            "voided_at": self.voided_at.isoformat() if self.voided_at else None,
            "voided_by_user_id": self.voided_by_user_id,
            "void_reason": self.void_reason,
            "is_active": self.is_active,
        }
