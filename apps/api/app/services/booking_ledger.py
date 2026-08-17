# app/services/booking_ledger.py
"""Reading and writing the booking payment ledger.

One place computes ledger totals, so the refund ceiling, the revenue figures
and anything built later cannot drift apart the way the duplicated
lifetime-revenue calculations once did.

The ledger is authoritative **only for bookings that have rows in it**. After
the backfill, most bookings will not: the source data to reconstruct their
payments does not exist and inventing it was explicitly out of scope. Callers
therefore ask `has_ledger()` first and fall back to the existing behaviour,
which is what keeps this change invisible for legacy records.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy import event, func, select

from app.extensions import db
from app.models.booking_transaction import (
    ENTRY_PAYMENT,
    ENTRY_REFUND,
    BookingTransaction,
)


class LedgerImmutableError(RuntimeError):
    """Raised when something tries to modify a recorded transaction."""


@dataclass(frozen=True)
class LedgerTotals:
    """What a booking's ledger says, in the booking's own currency.

    **An empty ledger means "no payment evidence was recorded", not "the
    customer paid zero".** The two are indistinguishable if you read
    `total_paid` alone -- it is 0.0 in both cases -- and treating the first as
    the second would invent a settled debt for most of the bookings in this
    system, because the historical data to reconstruct their payments does not
    exist. Check `total_paid_is_known` (or `has_ledger()`) before drawing any
    conclusion from a zero.
    """

    currency: str
    total_paid: float
    total_refunded: float
    entry_count: int

    @property
    def remaining_refundable(self) -> float:
        return max(0.0, self.total_paid - self.total_refunded)

    @property
    def has_entries(self) -> bool:
        return self.entry_count > 0

    @property
    def total_paid_is_known(self) -> bool:
        """Whether `total_paid` is a fact or merely the absence of evidence."""
        return self.entry_count > 0


def _round_money(value: float) -> float:
    return round(float(value) + 0.0, 2)


def live_transactions_query(booking_id: str):
    """Transactions that still stand: excludes reversals and what they reverse.

    A reversal and its target cancel out. Keeping both rows preserves the
    audit trail; excluding both from the sums is what makes the totals equal
    "what is actually true now".
    """
    reversed_ids = select(BookingTransaction.reverses_id).where(
        BookingTransaction.booking_id == booking_id,
        BookingTransaction.reverses_id.isnot(None),
    )
    return (
        db.session.query(BookingTransaction)
        .filter(BookingTransaction.booking_id == booking_id)
        .filter(BookingTransaction.reverses_id.is_(None))
        .filter(~BookingTransaction.transaction_id.in_(reversed_ids))
    )


def ledger_totals(booking_id: str, currency: str = "") -> LedgerTotals:
    """Sum a booking's standing transactions."""
    paid = 0.0
    refunded = 0.0
    count = 0
    resolved_currency = str(currency or "").strip().upper()
    for entry in live_transactions_query(booking_id).all():
        count += 1
        amount = float(entry.amount or 0.0)
        if not math.isfinite(amount) or amount <= 0:
            continue
        if not resolved_currency:
            resolved_currency = str(entry.currency or "").strip().upper()
        if entry.entry_type == ENTRY_PAYMENT:
            paid += amount
        elif entry.entry_type == ENTRY_REFUND:
            refunded += amount
    return LedgerTotals(
        currency=resolved_currency,
        total_paid=_round_money(paid),
        total_refunded=_round_money(refunded),
        entry_count=count,
    )


def has_ledger(booking_id: str) -> bool:
    """Whether this booking has any transactions at all.

    The discriminator between "ledger-backed" and "legacy" behaviour
    everywhere. Cheap on purpose -- it runs on the booking page and in the
    refund validation path.
    """
    if not booking_id:
        return False
    return db.session.execute(
        select(func.count(BookingTransaction.transaction_id)).where(
            BookingTransaction.booking_id == booking_id
        )
    ).scalar_one() > 0


def outstanding_balance(booking_value: float | None, totals: LedgerTotals) -> float | None:
    """Booking value less what has been paid. Negative means overpaid.

    None when the booking cannot be priced -- "we do not know" is not the
    same as "nothing outstanding", and reporting zero there would invent a
    settled balance.
    """
    if booking_value is None:
        return None
    return _round_money(booking_value - totals.total_paid)


# --------------------------------------------------------------------------
# Immutability
# --------------------------------------------------------------------------


def _guard_ledger_immutability(session, flush_context, instances) -> None:
    for obj in session.dirty:
        if isinstance(obj, BookingTransaction) and session.is_modified(obj, include_collections=False):
            raise LedgerImmutableError(
                f"Booking transaction {obj.public_ref or obj.transaction_id} cannot be modified. "
                "Record a reversal instead so the original entry survives."
            )
    for obj in session.deleted:
        if isinstance(obj, BookingTransaction):
            raise LedgerImmutableError(
                f"Booking transaction {obj.public_ref or obj.transaction_id} cannot be deleted. "
                "Record a reversal instead so the original entry survives."
            )


def _assign_public_refs(session, flush_context) -> None:
    """Fill in public_ref once the serial primary key exists.

    after_flush rather than before_flush because transaction_id is only
    assigned by the INSERT itself.
    """
    pending = [
        obj for obj in session.new
        if isinstance(obj, BookingTransaction) and not obj.public_ref and obj.transaction_id
    ]
    for entry in pending:
        session.execute(
            BookingTransaction.__table__.update()
            .where(BookingTransaction.__table__.c.transaction_id == entry.transaction_id)
            .values(public_ref=entry.build_public_ref())
        )
        # Keep the in-memory object consistent without marking it dirty --
        # the immutability guard would otherwise reject our own write.
        entry.__dict__["public_ref"] = entry.build_public_ref()


_listeners_registered = False


def register_booking_ledger_listeners() -> None:
    """Attach the immutability guard and the public-ref filler.

    Idempotent: create_app() runs repeatedly across the test suite.
    """
    global _listeners_registered
    if _listeners_registered:
        return
    event.listen(db.session, "before_flush", _guard_ledger_immutability)
    event.listen(db.session, "after_flush", _assign_public_refs)
    _listeners_registered = True
