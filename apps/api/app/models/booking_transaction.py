# app/models/booking_transaction.py
"""Immutable payment and refund transactions for a booking.

The CRM has never been able to say how much a traveler actually paid. A
booking carries a categorical `payment_status` and a single overwritten
`refund_amount` scalar -- no amounts, no dates, no history. This table is
where that history goes.

Design notes worth knowing before changing anything here:

* **One table, two entry types.** Payments and refunds share every column but
  `reason`, and a booking's money history is one chronological list. Two
  tables would mean a UNION for every read and two sequences to reconcile.

* **Append-only.** Nothing updates or deletes a row. A mistake is corrected by
  inserting a reversal that points back at the original via `reverses_id`,
  so the record of what was believed at the time survives. Enforced by a
  session guard in app/services/booking_ledger.py, not by convention.

* **Inferred rows are labelled.** Most of this system's history cannot be
  recovered -- the source workbook recorded real payment amounts for 31 of
  850 bookings. Anything reconstructed rather than recorded carries
  `is_inferred=True` and a `source` saying where it came from, so a
  reconstructed figure can never be mistaken for a receipt.

* **Float, not Numeric.** Every other monetary column in this schema is a
  float, and `parse_money`, `format_money` and the revenue rules all assume
  it. Introducing Decimal here alone would leak Decimal objects into all of
  them. Amounts are rounded to 2dp on write instead. Moving the whole system
  to Numeric is a worthwhile separate change, not a side effect of this one.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


ENTRY_PAYMENT = "payment"
ENTRY_REFUND = "refund"
ENTRY_TYPES = (ENTRY_PAYMENT, ENTRY_REFUND)

# Where a row came from. Anything beginning "migration_" was reconstructed
# during the backfill rather than recorded by an employee.
SOURCE_CRM_UI = "crm_ui"
SOURCE_CORRECTION = "correction"
SOURCE_MIGRATION_WORKBOOK = "migration_workbook"
SOURCE_MIGRATION_LEGACY_REFUND = "migration_legacy_refund"
SOURCE_MIGRATION_STATUS_INFERRED = "migration_status_inferred"

# 'exact' means the date is the real one. 'unknown' means the amount is
# trustworthy but the date is a placeholder -- true of every migrated refund,
# because the legacy scalar carried no date at all.
PRECISION_EXACT = "exact"
PRECISION_UNKNOWN = "unknown"


class BookingTransaction(db.Model):
    __tablename__ = "booking_transactions"

    transaction_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    public_ref = db.Column(db.String(24), unique=True, index=True)
    booking_id = db.Column(db.String(50), db.ForeignKey("trip_bookings.booking_id"), nullable=False, index=True)
    traveler_id = db.Column(db.String(20), db.ForeignKey("travelers.traveler_id"), index=True)

    entry_type = db.Column(db.String(16), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), nullable=False)

    occurred_on = db.Column(db.Date, nullable=False)
    date_precision = db.Column(db.String(16), nullable=False, default=PRECISION_EXACT)

    method = db.Column(db.String(40))
    reference = db.Column(db.String(120))
    reason = db.Column(db.Text)
    notes = db.Column(db.Text)

    source = db.Column(db.String(32), nullable=False, default=SOURCE_CRM_UI)
    is_inferred = db.Column(db.Boolean, nullable=False, default=False)
    reverses_id = db.Column(db.Integer, db.ForeignKey("booking_transactions.transaction_id"), index=True)

    idempotency_key = db.Column(db.String(200), unique=True, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now, index=True)

    # Declared so the unit of work knows a transaction INSERT depends on the
    # rows it points at -- the same omission that let BookingEventTrail be
    # inserted before its booking and fail the foreign key on Postgres.
    booking = db.relationship("TripBooking", foreign_keys=[booking_id])
    traveler = db.relationship("Traveler", foreign_keys=[traveler_id])
    created_by_user = db.relationship("User", foreign_keys=[created_by_user_id])
    reverses = db.relationship("BookingTransaction", remote_side=[transaction_id])

    __table_args__ = (
        db.Index("ix_booking_transactions_booking_entry", "booking_id", "entry_type"),
        db.CheckConstraint("amount > 0", name="ck_booking_transactions_amount_positive"),
        db.CheckConstraint(
            "entry_type IN ('payment', 'refund')", name="ck_booking_transactions_entry_type"
        ),
    )

    def __repr__(self) -> str:
        return f"<BookingTransaction {self.public_ref} {self.entry_type} {self.amount} {self.currency}>"

    @property
    def is_payment(self) -> bool:
        return self.entry_type == ENTRY_PAYMENT

    @property
    def is_refund(self) -> bool:
        return self.entry_type == ENTRY_REFUND

    @property
    def date_is_estimated(self) -> bool:
        return self.date_precision == PRECISION_UNKNOWN

    def build_public_ref(self) -> str:
        """Human-readable id, derived from the serial after insert.

        Support conversations need to name a transaction. Derived rather than
        allocated so there is no read-max-then-increment race -- the pattern
        _next_prefixed_id uses is fine for a booking and not for money.
        """
        prefix = "PMT" if self.is_payment else "RFD"
        return f"{prefix}-{int(self.transaction_id):06d}"

    def to_dict(self) -> dict:
        return {
            "transaction_id": self.transaction_id,
            "public_ref": self.public_ref,
            "booking_id": self.booking_id,
            "traveler_id": self.traveler_id,
            "entry_type": self.entry_type,
            "amount": self.amount,
            "currency": self.currency,
            "occurred_on": self.occurred_on.isoformat() if self.occurred_on else None,
            "date_precision": self.date_precision,
            "method": self.method,
            "reference": self.reference,
            "reason": self.reason,
            "notes": self.notes,
            "source": self.source,
            "is_inferred": bool(self.is_inferred),
            "reverses_id": self.reverses_id,
            "created_by_user_id": self.created_by_user_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
