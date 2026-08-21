# app/models/private_trip_transaction.py
"""Immutable payment and refund transactions for a private trip request.

The private-trip pipeline could record that a deposit had been taken and
nothing about the money itself: one `deposit_amount` scalar, no instalments,
no balance, no refunds, and no way to answer "how much has this customer
actually paid us". Revenue could only be reported by converting the request
into a synthetic Trip + TripBooking, which is exactly the double-counting the
conversion step was retired to prevent.

This table is the private-trip equivalent of booking_transactions, and follows
it deliberately -- same append-only rule, same reversal mechanics, same
`public_ref` derived from the serial after insert -- so the two ledgers behave
identically and anyone who knows one knows the other. Kept as a separate table
rather than a nullable `private_request_id` on booking_transactions because
that column is NOT NULL and every reader of it assumes a booking exists;
loosening it would put a "which parent is this?" branch into the refund
ceiling, the immutability guard and every revenue read.

Amounts are floats, matching every other monetary column in this schema and
what parse_money/format_money/the revenue rules all assume. Rounded to 2dp on
write. Moving the whole system to Numeric is a worthwhile separate change.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db

# Deliberately the same vocabulary as booking_transactions, imported rather
# than restated so a new entry type can never mean one thing in one ledger and
# something else in the other.
from app.models.booking_transaction import (  # noqa: F401  (re-exported for callers)
    ENTRY_PAYMENT,
    ENTRY_REFUND,
    ENTRY_TYPES,
    PRECISION_EXACT,
    PRECISION_UNKNOWN,
)

SOURCE_CRM_UI = "crm_ui"
SOURCE_CORRECTION = "correction"
SOURCE_PRIVATE_DEPOSIT_STAGE = "private_deposit_stage"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PrivateTripTransaction(db.Model):
    __tablename__ = "private_trip_transactions"

    transaction_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    public_ref = db.Column(db.String(24), unique=True, index=True)
    request_id = db.Column(
        db.String(20), db.ForeignKey("private_trip_requests.request_id"), nullable=False, index=True
    )
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
    # A private-trip deposit is non-refundable by policy once design work has
    # started, and the refund ceiling reads this the same way the booking
    # ledger does.
    is_non_refundable = db.Column(db.Boolean, nullable=False, default=False)
    reverses_id = db.Column(
        db.Integer, db.ForeignKey("private_trip_transactions.transaction_id"), index=True
    )

    idempotency_key = db.Column(db.String(200), unique=True, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_by = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_now, index=True)

    request = db.relationship("PrivateTripRequest", foreign_keys=[request_id])
    traveler = db.relationship("Traveler", foreign_keys=[traveler_id])
    created_by_user = db.relationship("User", foreign_keys=[created_by_user_id])
    reverses = db.relationship("PrivateTripTransaction", remote_side=[transaction_id])

    __table_args__ = (
        db.Index("ix_private_trip_transactions_request_entry", "request_id", "entry_type"),
        db.CheckConstraint("amount > 0", name="ck_private_trip_transactions_amount_positive"),
        db.CheckConstraint(
            "entry_type IN ('payment', 'refund')", name="ck_private_trip_transactions_entry_type"
        ),
    )

    def __repr__(self) -> str:
        return f"<PrivateTripTransaction {self.public_ref} {self.entry_type} {self.amount} {self.currency}>"

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

        Distinct prefixes from the booking ledger (PMT/RFD) so a reference in
        a support conversation identifies one record in one ledger.
        """
        prefix = "PTP" if self.is_payment else "PTR"
        return f"{prefix}-{int(self.transaction_id):06d}"

    def to_dict(self) -> dict:
        return {
            "transaction_id": self.transaction_id,
            "public_ref": self.public_ref,
            "request_id": self.request_id,
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
            "is_non_refundable": bool(self.is_non_refundable),
            "reverses_id": self.reverses_id,
            "created_by_user_id": self.created_by_user_id,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
