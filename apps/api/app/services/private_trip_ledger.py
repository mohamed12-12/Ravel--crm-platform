# app/services/private_trip_ledger.py
"""Reading and writing the private trip payment ledger.

The private-trip twin of app/services/booking_ledger.py, and deliberately the
same shape: one place computes the totals, so the refund ceiling, the money
panel on the request page, the traveler profile and Revenue Analytics cannot
drift apart.

The one difference that matters: a private request's ledger is authoritative
from the start. There is no historical backfill and no legacy scalar to fall
back on -- a private request has exactly the money that was recorded against
it, so an empty ledger really does mean "nothing has been paid yet".
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from sqlalchemy import event, func, select

from app.extensions import db
from app.models.private_trip_transaction import (
    ENTRY_PAYMENT,
    ENTRY_REFUND,
    PrivateTripTransaction,
)


class PrivateLedgerImmutableError(RuntimeError):
    """Raised when something tries to modify a recorded transaction."""


@dataclass(frozen=True)
class PrivateLedgerTotals:
    """What a private request's ledger says, in the request's own currency."""

    currency: str
    total_paid: float
    non_refundable_paid: float
    total_refunded: float
    entry_count: int
    payment_count: int
    refund_count: int
    first_payment_on: date | None = None
    last_payment_on: date | None = None
    # Set when standing entries disagree about which currency this request is
    # denominated in. Impossible through the write path (which pins every
    # entry to the request's currency) and reported rather than netted if it
    # ever shows up in data written another way.
    currencies_seen: tuple[str, ...] = ()

    @property
    def refundable_paid(self) -> float:
        return max(0.0, self.total_paid - self.non_refundable_paid)

    @property
    def remaining_refundable(self) -> float:
        return max(0.0, self.refundable_paid - self.total_refunded)

    @property
    def has_entries(self) -> bool:
        return self.entry_count > 0

    @property
    def has_currency_conflict(self) -> bool:
        return len(self.currencies_seen) > 1


def _round_money(value: float) -> float:
    return round(float(value) + 0.0, 2)


def live_transactions_query(request_id: str):
    """Transactions that still stand: excludes reversals and what they reverse.

    A reversal and its target cancel out. Keeping both rows preserves the
    audit trail; excluding both from the sums is what makes the totals equal
    "what is actually true now".
    """
    reversed_ids = select(PrivateTripTransaction.reverses_id).where(
        PrivateTripTransaction.request_id == request_id,
        PrivateTripTransaction.reverses_id.isnot(None),
    )
    return (
        db.session.query(PrivateTripTransaction)
        .filter(PrivateTripTransaction.request_id == request_id)
        .filter(PrivateTripTransaction.reverses_id.is_(None))
        .filter(~PrivateTripTransaction.transaction_id.in_(reversed_ids))
    )


def all_transactions(request_id: str) -> list[PrivateTripTransaction]:
    """Every entry, reversals included, newest first -- for the audit trail."""
    if not request_id:
        return []
    return (
        PrivateTripTransaction.query.filter(PrivateTripTransaction.request_id == request_id)
        .order_by(
            PrivateTripTransaction.occurred_on.desc(),
            PrivateTripTransaction.transaction_id.desc(),
        )
        .all()
    )


def summarize(entries) -> PrivateLedgerTotals:
    """Total a collection of standing transactions.

    Split out from ledger_totals() so a caller that has already loaded many
    requests' transactions in one query (the board, Revenue Analytics) totals
    them with exactly the same arithmetic instead of issuing a query per row.
    """
    paid = 0.0
    non_refundable_paid = 0.0
    refunded = 0.0
    count = 0
    payments = 0
    refunds = 0
    currency = ""
    seen: list[str] = []
    payment_dates: list[date] = []
    for entry in entries or ():
        count += 1
        amount = float(entry.amount or 0.0)
        if not math.isfinite(amount) or amount <= 0:
            continue
        code = str(entry.currency or "").strip().upper()
        if code and code not in seen:
            seen.append(code)
        if not currency:
            currency = code
        if entry.entry_type == ENTRY_PAYMENT:
            paid += amount
            payments += 1
            if bool(getattr(entry, "is_non_refundable", False)):
                non_refundable_paid += amount
            if entry.occurred_on:
                payment_dates.append(entry.occurred_on)
        elif entry.entry_type == ENTRY_REFUND:
            refunded += amount
            refunds += 1
    return PrivateLedgerTotals(
        currency=currency,
        total_paid=_round_money(paid),
        non_refundable_paid=_round_money(non_refundable_paid),
        total_refunded=_round_money(refunded),
        entry_count=count,
        payment_count=payments,
        refund_count=refunds,
        first_payment_on=min(payment_dates) if payment_dates else None,
        last_payment_on=max(payment_dates) if payment_dates else None,
        currencies_seen=tuple(seen),
    )


def ledger_totals(request_id: str) -> PrivateLedgerTotals:
    """Sum one private request's standing transactions."""
    if not request_id:
        return summarize([])
    return summarize(live_transactions_query(request_id).all())


def has_ledger(request_id: str) -> bool:
    if not request_id:
        return False
    return db.session.execute(
        select(func.count(PrivateTripTransaction.transaction_id)).where(
            PrivateTripTransaction.request_id == request_id
        )
    ).scalar_one() > 0


def standing_transactions_for(request_ids) -> dict[str, list[PrivateTripTransaction]]:
    """Standing (non-reversed) entries for many requests, in one query.

    The board and Revenue Analytics both need every request's money at once;
    issuing live_transactions_query() per request is what turns a 200-row
    board into 200 queries.
    """
    ids = [rid for rid in (request_ids or []) if rid]
    if not ids:
        return {}
    rows = (
        PrivateTripTransaction.query.filter(PrivateTripTransaction.request_id.in_(ids))
        .order_by(PrivateTripTransaction.occurred_on.asc(), PrivateTripTransaction.transaction_id.asc())
        .all()
    )
    reversed_ids = {row.reverses_id for row in rows if row.reverses_id}
    grouped: dict[str, list[PrivateTripTransaction]] = {rid: [] for rid in ids}
    for row in rows:
        if row.reverses_id is not None or row.transaction_id in reversed_ids:
            continue
        grouped.setdefault(row.request_id, []).append(row)
    return grouped


def totals_for(request_ids) -> dict[str, PrivateLedgerTotals]:
    """Ledger totals for many requests, in one query."""
    grouped = standing_transactions_for(request_ids)
    return {request_id: summarize(entries) for request_id, entries in grouped.items()}


# --------------------------------------------------------------------------
# Immutability -- identical contract to the booking ledger
# --------------------------------------------------------------------------


def _guard_ledger_immutability(session, flush_context, instances) -> None:
    for obj in session.dirty:
        if isinstance(obj, PrivateTripTransaction) and session.is_modified(obj, include_collections=False):
            raise PrivateLedgerImmutableError(
                f"Private trip transaction {obj.public_ref or obj.transaction_id} cannot be modified. "
                "Record a reversal instead so the original entry survives."
            )
    for obj in session.deleted:
        if isinstance(obj, PrivateTripTransaction):
            raise PrivateLedgerImmutableError(
                f"Private trip transaction {obj.public_ref or obj.transaction_id} cannot be deleted. "
                "Record a reversal instead so the original entry survives."
            )


def _assign_public_refs(session, flush_context) -> None:
    """Fill in public_ref once the serial primary key exists."""
    pending = [
        obj for obj in session.new
        if isinstance(obj, PrivateTripTransaction) and not obj.public_ref and obj.transaction_id
    ]
    for entry in pending:
        session.execute(
            PrivateTripTransaction.__table__.update()
            .where(PrivateTripTransaction.__table__.c.transaction_id == entry.transaction_id)
            .values(public_ref=entry.build_public_ref())
        )
        # Keep the in-memory object consistent without marking it dirty --
        # the immutability guard would otherwise reject our own write.
        entry.__dict__["public_ref"] = entry.build_public_ref()


_listeners_registered = False


def register_private_trip_ledger_listeners() -> None:
    """Attach the immutability guard and the public-ref filler.

    Idempotent: create_app() runs repeatedly across the test suite.
    """
    global _listeners_registered
    if _listeners_registered:
        return
    event.listen(db.session, "before_flush", _guard_ledger_immutability)
    event.listen(db.session, "after_flush", _assign_public_refs)
    _listeners_registered = True
